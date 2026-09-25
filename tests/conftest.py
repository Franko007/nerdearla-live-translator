"""Fixtures compartidos para tests."""
from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest


def make_wav(path: Path, seconds: float = 8.0, freq: int = 440) -> Path:
    """Genera un WAV válido 16 kHz / 16-bit / mono sin depender de ffmpeg."""
    frames = bytearray()
    n = int(16000 * seconds)
    for i in range(n):
        frames += struct.pack("<h", int(32767 * 0.25 * math.sin(2 * math.pi * freq * i / 16000)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(frames))
    return path


def build_replay(dir: Path, session_id: str, title: str, talk: list[tuple[int, int, str, str]]) -> Path:
    """Escribe <id>.wav + <id>.replay.json. talk = (start_ms, end_ms, en, es)[]."""
    make_wav(dir / f"{session_id}.wav", seconds=6)
    events = []
    segments = []
    for seq, (start, end, en, es) in enumerate(talk, start=1):
        segments.append({
            "seq": seq,
            "start_ms": start,
            "end_ms": end,
            "source_text": en,
            "translations": {"es": es},
        })
        events.append({"at_ms": start + 200, "kind": "partial", "text": en})
        events.append({"at_ms": end, "kind": "final", "text": en, "start_ms": start, "end_ms": end})
    data = {
        "version": 1,
        "session_id": session_id,
        "title": title,
        "source_lang": "en",
        "target_langs": ["es"],
        "audio": f"samples/{session_id}.wav",
        "events": events,
        "segments": segments,
    }
    out = dir / f"{session_id}.replay.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


@pytest.fixture
def short_talk() -> list[tuple[int, int, str, str]]:
    return [
        (200, 900, "Hello everyone, welcome to the demo.", "Hola a todos, bienvenidos a la demo."),
        (1200, 2100, "This is a second closed segment.", "Este es el segundo segmento cerrado."),
        (2400, 3300, "And a third one with technical terms: Kubernetes and OpenTelemetry.",
         "Y un tercero con términos técnicos: Kubernetes y OpenTelemetry."),
    ]