"""Integración end-to-end: app completa en modo Replay sobre un servidor HTTP real.

Levanta la app en un uvicorn real (loop aislado en la misma corrutina), espera a
que las sesiones terminen y valida API, exportación y SSE (buffer + Last-Event-ID)
como lo haría un navegador/OBS. Se usa HTTP real y no ASGITransport porque un
stream SSE infinito hace que el transporte ASGI bufferée y nunca retorne.
"""
from __future__ import annotations

import asyncio
import socket
import threading

import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import build_replay

TALK = [
    (200, 900, "Hello everyone, welcome to the demo.", "Hola a todos, bienvenidos a la demo."),
    (1200, 2100, "Second segment with Kubernetes.", "Segundo segmento con Kubernetes."),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _collect_sse(client, url, want: int, headers=None, timeout: float = 6.0) -> list[str]:
    """Lee líneas SSE hasta juntar `want` eventos data (o timeout)."""
    lines: list[str] = []
    async with client.stream("GET", url, headers=headers or {}) as resp:
        assert resp.status_code == 200
        try:
            async with asyncio.timeout(timeout):
                async for line in resp.aiter_lines():
                    lines.append(line)
                    if len([l for l in lines if l.startswith("data: ")]) >= want:
                        break
        except (TimeoutError, asyncio.TimeoutError):
            pass
    return lines


@pytest.fixture(scope="module")
async def app_client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("replay")
    build_replay(tmp, "talk-a", "Talk Demo A", TALK)
    settings = Settings(provider="replay", samples_dir=tmp, env_file=None)
    app = create_app(settings)
    client = await _serve(app)
    client.samples_dir = tmp
    yield client


async def _serve(app):
    """Levanta la app en un uvicorn real dentro de un thread con su propio loop.

    Evita el choque de event loops de pytest-asyncio (uno por test) y permite
    HTTP/WS reales.
    """
    import uvicorn
    from httpx import AsyncClient, Limits

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True, name=f"uvicorn:{port}")
    thread.start()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while loop.time() < deadline:
        if server.started:
            break
        await asyncio.sleep(0.05)

    # Sin keep-alive: cada request abre y cierra su conexión, así el cliente no
    # reutiliza sockets cerrados por el event loop del test anterior.
    client = AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        limits=Limits(max_connections=16, max_keepalive_connections=0),
    )
    client.port = port
    client._server = server
    client._thread = thread
    return client


async def _teardown(client):
    await client.aclose()
    client._server.should_exit = True
    client._thread.join(timeout=10)


@pytest.fixture(scope="module")
async def ws_client(tmp_path_factory):
    """Servidor LIVE con un transcriber/translator fake: testea la ingesta por WS.

    Parchea app.session.build_adapters en el módulo ya importado, así el server
    (que corre en otro thread, mismo proceso) usa el fake y no toca Gemini.
    """
    import app.session as session_mod

    class _FakeTranscriber:
        async def stream(self, audio):
            total = 0
            async for chunk in audio:
                total += len(chunk)
            if total:
                from app.models import TranscriptEvent

                yield TranscriptEvent(text="Hola desde el mic.", is_final=True, start_ms=0, end_ms=500)

        async def close(self):
            pass

    class _FakeTranslator:
        async def translate(self, text, target):
            return f"[{target}] Hola traducido."

    def fake_build(settings, session, glossary, client):
        return _FakeTranscriber(), _FakeTranslator(), "live"

    original = session_mod.build_adapters
    session_mod.build_adapters = fake_build
    client = None
    try:
        settings = Settings(provider="gemini", gemini_api_key="test", env_file=None)
        client = await _serve(create_app(settings))
        yield client
    finally:
        session_mod.build_adapters = original
        if client is not None:
            await _teardown(client)


async def _wait_finished(client, timeout: float = 12.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        payload = (await client.get("/api/sessions")).json()
        if all(s["status"] in ("finished", "error", "stopped") for s in payload["sessions"]):
            return payload
        await asyncio.sleep(0.2)
    raise TimeoutError("las sesiones no terminaron a tiempo")


async def test_replay_session_full_pipeline(app_client):
    payload = await _wait_finished(app_client)
    s = payload["sessions"][0]
    assert s["mode"] == "DEMO"
    assert s["status"] == "finished"
    assert s["segments"] == len(TALK)
    assert s["metrics"]["translation_calls"] == len(TALK)
    assert s["error"] is None


async def test_export_srt(app_client):
    await _wait_finished(app_client)
    resp = await app_client.get("/export/talk-a.srt?lang=es")
    assert resp.status_code == 200
    assert "Hola a todos, bienvenidos a la demo." in resp.text
    assert "--> " in resp.text
    assert resp.headers["content-type"].startswith("application/x-subrip")


async def test_export_vtt_and_txt(app_client):
    await _wait_finished(app_client)
    vtt = await app_client.get("/export/talk-a.vtt?lang=es")
    assert vtt.status_code == 200 and vtt.text.startswith("WEBVTT")
    txt = await app_client.get("/export/talk-a.txt?lang=es")
    assert "Segundo segmento con Kubernetes." in txt.text


async def test_sse_replays_buffer_with_last_event_id(app_client):
    await _wait_finished(app_client)
    lines = await _collect_sse(
        app_client, "/stream/talk-a?lang=es", want=len(TALK), headers={"Last-Event-ID": "0"}
    )
    data = [l for l in lines if l.startswith("data: ")]
    assert len(data) >= len(TALK)
    ids = [l for l in lines if l.startswith("id: ")]
    assert all(int(i.split()[1]) > 0 for i in ids)


async def test_sse_respects_last_event_id(app_client):
    await _wait_finished(app_client)
    # Traducciones en seq 3 y 6 (partial 1, final 2, translation 3). Last seq 3 -> solo seq 6.
    lines = await _collect_sse(app_client, "/stream/talk-a?lang=es", want=1, headers={"Last-Event-ID": "3"})
    ids = [int(l.split()[1]) for l in lines if l.startswith("id: ")]
    assert ids == [6]


async def test_stream_rejects_invalid_lang(app_client):
    await _wait_finished(app_client)
    resp = await app_client.get("/stream/talk-a?lang=xx")
    assert resp.status_code == 400


async def test_post_creates_session(app_client):
    # Fija el bug del POST 422: FastAPI trataba `body` como param de query porque
    # el modelo estaba definido como clase local dentro de create_app().
    build_replay(app_client.samples_dir, "talk-b", "Talk Demo B", TALK)
    wav = app_client.samples_dir / "talk-b.wav"
    resp = await app_client.post(
        "/api/sessions",
        json=dict(source=str(wav), session_id="talk-b", auto_start=False),
    )
    assert resp.status_code == 200
    s = resp.json()
    assert s["id"] == "talk-b"
    assert s["status"] == "created"


async def test_stream_rejects_bad_body(app_client):
    resp = await app_client.post("/api/sessions", json={})
    assert resp.status_code == 422
    assert "source" in resp.text


async def test_healthz(app_client):
    resp = await app_client.get("/healthz")
    assert resp.json()["mode"] == "demo"


async def test_pages(app_client):
    index = await app_client.get("/")
    assert index.status_code == 200 and "text/html" in index.headers["content-type"]
    admin = await app_client.get("/admin")
    assert admin.status_code == 200
    # El nombre del path param debe coincidir con el argumento del handler.
    overlay = await app_client.get("/overlay/talk-a")
    assert overlay.status_code == 200
    # La lib de QR está vendored y se sirve offline.
    qr = await app_client.get("/static/qrcode-generator.min.js")
    assert qr.status_code == 200 and b"qrcode" in qr.content


async def test_delete_session(app_client):
    build_replay(app_client.samples_dir, "talk-c", "Talk Demo C", TALK)
    wav = app_client.samples_dir / "talk-c.wav"
    resp = await app_client.post(
        "/api/sessions", json=dict(source=str(wav), session_id="talk-c", auto_start=False)
    )
    assert resp.status_code == 200
    deleted = await app_client.delete("/api/sessions/talk-c")
    assert deleted.status_code == 200
    assert (await app_client.get("/api/sessions/talk-c")).status_code == 404


# ------------------------------------------------------------- ingesta por WS
def _ws_url(port: int, session_id: str, **params) -> str:
    import urllib.parse

    qs = urllib.parse.urlencode(params)
    return f"ws://127.0.0.1:{port}/ws/audio/{session_id}" + (f"?{qs}" if qs else "")


async def _wait_session(client, session_id, status="finished", timeout: float = 10.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        payload = (await client.get("/api/sessions")).json()
        s = next((x for x in payload["sessions"] if x["id"] == session_id), None)
        if s is not None and s["status"] == status:
            return s
        await asyncio.sleep(0.2)
    raise TimeoutError(f"la sesión {session_id} no llegó a {status}")


async def test_ws_audio_creates_session_and_finishes(ws_client):
    import websockets

    uri = _ws_url(ws_client.port, "sala-1", source_lang="en", target_lang="es")
    async with websockets.client.connect(uri) as ws:
        # ~0,5 s de silencio PCM 16k; alcanza para que el fake emita un final.
        await ws.send(b"\x00\x00" * 8000)
    s = await _wait_session(ws_client, "sala-1")
    assert s["source_lang"] == "en"
    assert "es" in s["target_langs"]
    assert s["mode"] == "LIVE"
    assert s["error"] is None

    # La traducción fake debe haber llegado por SSE al público.
    lines = await _collect_sse(ws_client, "/stream/sala-1?lang=es", want=1, headers={"Last-Event-ID": "0"})
    data = [l for l in lines if l.startswith("data: ")]
    assert any("[es]" in l for l in data)


async def test_ws_audio_rejects_second_capture(ws_client):
    import websockets
    import websockets.exceptions

    uri = _ws_url(ws_client.port, "sala-2", source_lang="es", target_lang="es")
    async with websockets.client.connect(uri) as ws1:
        await ws1.send(b"\x00\x00" * 8000)
        ws2 = await websockets.client.connect(uri)
        try:
            with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
                # el cierre del server se materializa al esperar datos
                await asyncio.wait_for(ws2.recv(), timeout=5)
            assert exc.value.rcvd is not None and exc.value.rcvd.code == 1008
        finally:
            await ws2.close()
    # al caerse ws1 la sesión termina sola
    await _wait_session(ws_client, "sala-2")


async def test_ws_audio_transcription_only_when_no_target(ws_client):
    import websockets

    uri = _ws_url(ws_client.port, "sala-3", source_lang="es")
    async with websockets.client.connect(uri) as ws:
        await ws.send(b"\x00\x00" * 8000)
    s = await _wait_session(ws_client, "sala-3")
    assert s["source_lang"] == "es"
    assert s["target_langs"] == ["es"]
    assert s["metrics"]["translation_calls"] == 0