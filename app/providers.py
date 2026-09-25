"""Fábricas de proveedores.

Aísla la elección de proveedor (gemini | replay) del resto del pipeline.
Todos los adaptadores exponen las mismas interfaces mínimas.
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.replay import load_replay, replay_path_for_audio
from app.transcribers.gemini import GeminiTranscriber, backend_model
from app.transcribers.replay import ReplayTranscriber
from app.translators.gemini import GeminiTranslator
from app.translators.replay import ReplayTranslator


def _translate_model(settings: Settings) -> str:
    """Modelo de traducción por backend.

    En este proyecto Vertex las generaciones 3.x no están habilitadas; se usa
    2.5-flash (validado). AI Studio sigue con el configurado (flash-lite).
    """
    if not settings.google_genai_use_vertexai:
        return settings.translate_model
    name = (settings.translate_model or "").strip()
    if name.startswith("publishers/"):
        return name
    if name.startswith("gemini-3") or not name:
        name = "gemini-2.5-flash"
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
    return (
        GeminiTranscriber(backend_model(settings, settings.transcribe_model), session.source_lang, client=client),
        GeminiTranslator(_translate_model(settings), glossary, client=client),
        "live",
    )