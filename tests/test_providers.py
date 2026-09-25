"""Tests de provistas de adaptadores (build_adapters)."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.providers import build_adapters


class _FakeSession:
    source = "https://example.com/stream.m3u8"


def test_replay_rejects_remote_source_with_hint():
    s = Settings(provider="replay", env_file=None)
    with pytest.raises(RuntimeError, match="[Pp]rovider de gemini|PROVIDER=gemini"):
        build_adapters(s, _FakeSession(), [], None)


def test_replay_ok_with_local_wav(tmp_path):
    import wave

    wav = tmp_path / "local.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)

    from app.replay import ReplayData

    ReplayData(
        session_id="local", title="Local", source_lang="en", target_langs=["es"],
        audio=str(wav), events=[], segments=[],
    ).save(wav.with_suffix(".replay.json"))

    class _S:
        source = str(wav)

    transcriber, translator, mode = build_adapters(Settings(provider="replay", env_file=None), _S(), [], None)
    assert mode == "demo"
    assert transcriber is not None and translator is not None