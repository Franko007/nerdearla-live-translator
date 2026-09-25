"""Fábricas de proveedores.

Aísla la elección de proveedor (gemini | replay) del resto del pipeline.
Todos los adaptadores exponen las mismas interfaces mínimas.
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.replay import load_replay, replay_path_for_audio
from app.transcribers.gemini import GeminiTranscriber, transcribe_model_for
from app.transcribers.replay import ReplayTranscriber
from app.translators.gemini import GeminiTranslator
from app.translators.replay import ReplayTranslator


def _translate_model(settings: Settings) -> str:
    """Modelo de traducción por backend.

    En este proyecto Vertex las generaciones 3.x no están habilitadas; se usa
    2.5-flash-lite (validado). AI Studio sigue con el configurado (flash-lite).
    """
    if not settings.google_genai_use_vertexai:
        return settings.translate_model
    name = (settings.translate_model or "").strip()
    if name.startswith("publishers/"):
        return name
    if name.startswith("gemini-3") or not name:
        name = "gemini-2.5-flash-lite"
    return f"publishers/google/models/{name}"


def build_adapters(settings: Settings, session, glossary: list[str], client=None):
    """Devuelve (transcriber, translator, mode). mode: 'live' | 'demo'."""
    if settings.is_replay:
        if not Path(session.source).is_file():
            raise RuntimeError(
                f"Modo DEMO (PROVIDER=replay) reproduce solo samples locales "
                f"(*.wav + *.replay.json); no puede leer '{session.source}'. "
                "Las URLs, YouTube y HLS requieren PROVIDER=gemini + GEMINI_API_KEY."
            )
        replay_path = replay_path_for_audio(session.source)
        if not replay_path.is_file():
            raise RuntimeError(f"No existe grabación de replay para {session.source}: {replay_path}")
        data = load_replay(replay_path)
        return ReplayTranscriber(data), ReplayTranslator(data), "demo"
    # "auto" (default) = chunked: los transcribe-*-live no son accesibles en este
    # proyecto (404 "no access" en Vertex / sin eventos en AI Studio). Live queda
    # como opt-in explícito para cuentas con acceso.
    chunked = settings.transcribe_mode != "live"
    tr_model = transcribe_model_for(settings, chunked)
    return (
        GeminiTranscriber(
            tr_model, session.source_lang, client=client,
            chunked=chunked, chunk_ms=settings.transcribe_chunk_ms,
        ),
        GeminiTranslator(_translate_model(settings), glossary, client=client),
        "live",
    )