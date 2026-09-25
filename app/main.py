"""FastAPI: HTTP, SSE, archivos estáticos.

Endpoints:
  /                             Frontend (audiencia + demo)
  /admin                        Panel de operación
  /overlay/{session_id}?lang=   Overlay para OBS Browser Source
  /stream/{session_id}?lang=    SSE de subtítulos (Last-Event-ID soportado)
  /api/sessions                 Crear/listar sesiones
  /export/{session_id}.{fmt}    SRT / VTT / TXT
  /healthz                      Health check
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.export import render_srt, render_txt, render_vtt
from app.glossary import load_glossary
from app.hub import Hub
from app.session_manager import SessionManager
from app.transcribers.gemini import build_gemini_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

STATIC_DIR = Path(__file__).parent / "static"
EXPORT_RENDER = {"srt": render_srt, "vtt": render_vtt, "txt": render_txt}


def create_app(settings: Settings | None = None, *, _entrypoint: bool = False) -> FastAPI:
    settings = settings or get_settings()
    hub = Hub()

    def _build_client():
        if settings.provider.lower() != "gemini":
            return None
        return build_gemini_client(settings)

    manager = SessionManager(settings, hub, load_glossary(settings.glossary_path), client=_build_client())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.is_replay:
            sessions = manager.discover_samples()
            for s in sessions:
                await s.start()
            if sessions:
                log.info("Modo DEMO - sesiones creadas: %s", ", ".join(s.id for s in sessions))
        yield
        for s in manager.all():
            await s.stop()

    app = FastAPI(title="Nerdearla Live Translator", version="0.1.0", lifespan=lifespan)

    # ------------------------------------------------------------- API
    @app.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "provider": settings.provider,
            "mode": "live" if not settings.is_replay else "demo",
            "sessions": len(manager.sessions),
        }

    @app.get("/api/sessions")
    async def list_sessions():
        return {
            "provider": settings.provider,
            "mode": "live" if not settings.is_replay else "demo",
            "sessions": manager.summaries(),
        }

    class CreateSessionBody(BaseModel):
        source: str
        session_id: str | None = None
        title: str = ""
        source_lang: str | None = None
        target_langs: list[str] | None = None
        auto_start: bool = True

    @app.post("/api/sessions")
    async def create_session(body: CreateSessionBody):
        try:
            session = await manager.create(
                source=body.source,
                session_id=body.session_id,
                title=body.title,
                source_lang=body.source_lang,
                target_langs=body.target_langs,
                auto_start=body.auto_start,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return session.summary()

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        session = manager.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada.")
        return session.summary()

    @app.post("/api/sessions/{session_id}/stop")
    async def stop_session(session_id: str):
        if not await manager.stop(session_id):
            raise HTTPException(status_code=404, detail="Sesión no encontrada.")
        return {"stopped": session_id}

    # ------------------------------------------------------------- SSE
    @app.get("/stream/{session_id}")
    async def stream(session_id: str, request: Request, lang: str | None = Query(default=None)):
        session = manager.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada.")
        language = _resolve_lang(session, lang)
        if language is None:
            raise HTTPException(
                status_code=400,
                detail=f"Idioma '{lang}' no es válido para esta sesión. Válidos: {sorted(set(session.target_langs) | {session.source_lang})}",
            )

        last_id = request.headers.get("last-event-id", "")

        async def gen() -> AsyncIterator[str]:
            last = int(last_id) if last_id.isdigit() else 0
            last_delivered = last
            # 1) buffer histórico para reconexión (Last-Event-ID) y primeros frames
            for ev in hub.events_since(session_id, last):
                if ev.seq <= last_delivered or ev.lang != language:
                    continue
                last_delivered = ev.seq
                yield ev.to_sse()
            # 2) en vivo
            async with hub.subscriber(session_id) as q:
                while True:
                    ev = await q.get()
                    if ev.seq <= last_delivered:
                        continue
                    last_delivered = ev.seq
                    if ev.lang == language:
                        yield ev.to_sse()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------- Export
    @app.get("/export/{session_id}.{fmt}")
    async def export(session_id: str, fmt: str, lang: str | None = None):
        session = manager.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada.")
        fmt = fmt.lower()
        renderer = EXPORT_RENDER.get(fmt)
        if renderer is None:
            raise HTTPException(status_code=400, detail=f"Formato no soportado: {fmt}. Usá srt, vtt o txt.")
        if lang is None:
            language = next((t for t in session.target_langs if t != session.source_lang), session.source_lang)
        else:
            language = _resolve_lang(session, lang)
            if language is None:
                raise HTTPException(status_code=400, detail=f"Idioma '{lang}' no es válido para esta sesión.")
        content = renderer(session.segments, language)
        media = {
            "srt": "application/x-subrip",
            "vtt": "text/vtt",
            "txt": "text/plain",
        }[fmt]
        filename = f"{session_id}.{fmt}"
        return StreamingResponse(
            iter([content]),
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ------------------------------------------------------------- Páginas
    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/admin")
    async def admin():
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/overlay/{session_id}")
    async def overlay(_session_id: str):
        return FileResponse(STATIC_DIR / "overlay.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    if _entrypoint:
        app.state.manager = manager

    return app


app = create_app(_entrypoint=True)


def _resolve_lang(session, lang: str | None) -> str | None:
    """Resuelve el idioma pedido; None si no es válido."""
    valid = set(session.target_langs) | {session.source_lang}
    if lang is None:
        return session.source_lang if session.source_lang in valid else (session.target_langs[0] if session.target_langs else None)
    return lang if lang in valid else None