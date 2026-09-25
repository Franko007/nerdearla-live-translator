"""Tests del transcriber Gemini: mapeo de backends y modo chunks (sin réseau)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import Settings
from app.transcribers.gemini import CHUNK_MS, GeminiTranscriber, backend_model


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents})
        um = SimpleNamespace(prompt_token_count=7, candidates_token_count=5)
        return SimpleNamespace(text="Hello world", usage_metadata=um)


class _FakeClient:
    def __init__(self) -> None:
        self.aio = SimpleNamespace(models=_FakeModels())

    @property
    def models_calls(self) -> list[dict]:
        return self.aio.models.calls


def _pcm(ms: int, sample_rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(sample_rate * ms / 1000)


class TestBackendModel:
    def test_ai_studio_keeps_short_name(self):
        s = Settings(env_file=None)
        assert backend_model(s, "gemini-3.5-transcribe-live") == "gemini-3.5-transcribe-live"

    def test_vertex_prefixes_and_renames(self):
        s = Settings(env_file=None, google_genai_use_vertexai=True)
        assert backend_model(s, "gemini-3.5-transcribe-live") == (
            "publishers/google/models/gemini-3.5-transcribe-live-preview"
        )


@pytest.mark.asyncio
async def test_stream_chunked_emits_final_per_window():
    client = _FakeClient()
    tr = GeminiTranscriber("gemini-fake", "en", client=client, chunked=True, chunk_ms=100)

    async def audio():
        yield _pcm(70)
        yield _pcm(70)   # 140 ms acumulados -> 1 ventana de 100ms + resto
        yield _pcm(70)   # resto 40 + 70 = 110 -> 2da ventana + resto 10

    events = [ev async for ev in tr.stream(audio())]
    assert len(events) == 2
    # Ventana 1: bytes 0..100ms
    assert events[0].start_ms == 0
    assert events[0].end_ms == 100
    assert events[0].is_final
    # Ventana 2: bytes 100..200ms (resto 40 + 70 = 110 >= 100)
    assert events[1].start_ms == 100
    assert events[1].end_ms == 200
    # Cada llamada recibe audio WAV (cabecera RIFF) + el prompt.
    calls = client.models_calls
    assert len(calls) == 2
    first = calls[0]["contents"].parts[0].inline_data
    assert first.mime_type == "audio/wav"
    assert first.data[:4] == b"RIFF"
    assert calls[0]["contents"].parts[1].text == (
        "Transcribe verbatim the speech in the audio. Output only the transcribed text, no commentary.\n"
        "The speech is in the language code: en."
    )


@pytest.mark.asyncio
async def test_chunked_respects_config_model_name():
    s = Settings(env_file=None)
    client = _FakeClient()
    tr = GeminiTranscriber(backend_model(s, s.translate_model), "en", client=client, chunked=True, chunk_ms=100)

    async def audio():
        yield _pcm(110)

    _ = [ev async for ev in tr.stream(audio())]
    assert client.models_calls[0]["model"] == s.translate_model


@pytest.mark.asyncio
async def test_chunked_emits_nothing_without_audio():
    client = _FakeClient()
    tr = GeminiTranscriber("gemini-fake", "en", client=client, chunked=True, chunk_ms=100)

    async def audio():
        yield b""

    events = [ev async for ev in tr.stream(audio())]
    assert events == []
    assert client.models_calls == []


@pytest.mark.asyncio
async def test_stream_dispatch_live_when_not_chunked():
    # Cuando NO es chunked, stream() no llama generate_content sino live.api.
    client = _FakeClient()

    async def boom():
        yield b""
        return
        yield b""  # pragma: no cover

    with patch("google.genai.types.LiveConnectConfig") as _cfg, pytest.raises(Exception):
        tr = GeminiTranscriber("gemini-fake", "en", client=client, chunked=False)
        async for _ in tr.stream(boom()):
            pass
    assert client.models_calls == []