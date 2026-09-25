"""Fábricas de proveedores.

Aísla la elección de proveedor (gemini | replay) del resto del pipeline.
Todos los adaptadores exponen las mismas interfaces mínimas.
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.replay import load_replay, replay_path_for_audio
from app.transcribers.gemini import GeminiTranscriber
from app.transcribers.replay import ReplayTranscriber
from app.translators.gemini import GeminiTranslator
from app.translators.replay import ReplayTranslator


def build_adapters(settings: Settings, session, glossary: list[str], client=None):
    """Devuelve (transcriber, translator, mode). mode: 'live' | 'demo'."""
    if settings.is_replay:
        if not Path(session.source).is_file():
            raise RuntimeError(f"Fuente de audio no encontrada: {session.source}")
        replay_path = replay_path_for_audio(session.source)
        if not replay_path.is_file():
            raise RuntimeError(f"No existe grabación de replay para {session.source}: {replay_path}")
        data = load_replay(replay_path)
        return ReplayTranscriber(data), ReplayTranslator(data), "demo"
    return (
        GeminiTranscriber(settings.transcribe_model, session.source_lang, client=client),
        GeminiTranslator(settings.translate_model, glossary, client=client),
        "live",
    )