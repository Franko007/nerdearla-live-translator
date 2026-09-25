"""Tests de exportación SRT/VTT/TXT y métricas."""
from __future__ import annotations

import pytest

from app.export import render_srt, render_txt, render_vtt
from app.metrics import Metrics, pct
from app.models import Segment


def segs() -> list[Segment]:
    return [
        Segment(
            seq=1,
            start_ms=100,
            end_ms=2500,
            source_text="Hello there.",
            translations={"es": "Hola."},
            segment_final_at=0.1,
            audio_pos_ms_at_close=2500,
        ),
        Segment(
            seq=2,
            start_ms=3000,
            end_ms=5400,
            source_text="See you soon.",
            translations={"es": "Nos vemos."},
            segment_final_at=0.2,
            audio_pos_ms_at_close=5400,
        ),
    ]


def test_srt_format():
    out = render_srt(segs(), "es")
    assert out == (
        "1\n00:00:00,100 --> 00:00:02,500\nHola.\n\n"
        "2\n00:00:03,000 --> 00:00:05,400\nNos vemos.\n"
    )


def test_vtt_format():
    out = render_vtt(segs(), "es")
    assert out.startswith("WEBVTT")
    assert "00:00:00.100 --> 00:00:02.500" in out
    assert "Hola." in out


def test_txt_format():
    out = render_txt(segs(), "es")
    assert out == "Hola.\nNos vemos.\n"


def test_fallback_when_translation_missing():
    # lang 'de' no existe -> usa source_text
    out = render_srt([segs()[0]], "de")
    assert "Hello there." in out
    assert "Hola." not in out


def test_pct():
    assert pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5
    assert pct([1, 2, 3, 4], 0.95) == 4
    assert pct([], 0.5) != pct([], 0.5)  # NaN


def test_metrics_snapshot():
    m = Metrics()
    m.transcription_ms = [100, 200, 300]
    m.translation_ms = [10, 20]
    m.pipeline_ms = [30]
    snap = m.snapshot()
    assert snap["p50_ms"]["transcription"] == 200
    assert snap["p95_ms"]["transcription"] == 300
    assert snap["p50_ms"]["translation"] == 10
    assert snap["translation_calls"] == 0