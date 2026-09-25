"""Integración end-to-end: app completa en modo Replay sin API key.

Levanta la app (lifespan incluido) contra un directorio de samples temporal,
espera a que las sesiones terminen y valida API, exportación y SSE (buffer +
Last-Event-ID).
"""
from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from tests.conftest import build_replay

# talk corto para que el test termine rápido
TALK = [
    (200, 900, "Hello everyone, welcome to the demo.", "Hola a todos, bienvenidos a la demo."),
    (1200, 2100, "Second segment with Kubernetes.", "Segundo segmento con Kubernetes."),
]


async def _collect_sse(client, url, want: int, headers=None, timeout: float = 5.0) -> list[str]:
    """Lee líneas SSE hasta juntar `want` eventos data (o timeout)."""
    lines: list[str] = []
    start = time.monotonic()
    async with client.stream("GET", url, headers=headers or {}) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            lines.append(line)
            if len([l for l in lines if l.startswith("data: ")]) >= want:
                break
            if time.monotonic() - start > timeout:
                break
    return lines


@pytest.fixture
async def app_client(tmp_path):
    build_replay(tmp_path, "talk-a", "Talk Demo A", TALK)
    settings = Settings(provider="replay", samples_dir=tmp_path, env_file=None)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _wait_finished(client, timeout: float = 12.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        payload = (await client.get("/api/sessions")).json()
        all_done = all(s["status"] in ("finished", "error", "stopped") for s in payload["sessions"])
        if all_done:
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
    # Sin Last-Event-ID: expone las traducciones del buffer (una por segmento)
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
    resp = await app_client.get("/stream/talk-a?lang=xx")
    assert resp.status_code == 400


async def test_healthz(app_client):
    resp = await app_client.get("/healthz")
    assert resp.json()["mode"] == "demo"