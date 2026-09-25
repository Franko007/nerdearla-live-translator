"""Tests del Segmenter: parciales visibles, traducción solo al cerrar."""
from __future__ import annotations

import asyncio

import pytest

from app.metrics import Metrics
from app.models import TranscriptEvent
from app.segmenter import Segmenter


class FakeTranslator:
    calls: list[tuple[str, str]] = []

    async def translate(self, source_text: str, target_lang: str) -> str:
        self.calls.append((source_text, target_lang))
        return f"[{target_lang}] {source_text}"


@pytest.fixture
def fake_translator():
    return FakeTranslator()


def partial(text: str, n: int = 1) -> TranscriptEvent:
    return TranscriptEvent(text=text, is_final=False, received_at=float(n) / 10, audio_pos_ms=n * 100)


def final(text: str, n: int, start_ms: int | None = None) -> TranscriptEvent:
    return TranscriptEvent(
        text=text, is_final=True, start_ms=start_ms, received_at=float(n) / 10, audio_pos_ms=n * 100
    )


async def test_partials_not_translated_but_final_is(fake_translator):
    seg = Segmenter("s1", "en", ["es"], fake_translator, Metrics(), origin_s=0.0)

    evs = await seg.feed(partial("Hello everyone, welcome to", 1), 100)
    assert [e.kind for e in evs] == ["partial"]
    assert fake_translator.calls == []

    evs = await seg.feed(partial("Hello everyone, welcome to the demo.", 2), 200)
    assert [e.kind for e in evs] == ["partial"]
    assert fake_translator.calls == []

    evs = await seg.feed(final("Hello everyone, welcome to the demo.", 3), 300)
    kinds = [e.kind for e in evs]
    assert kinds == ["final", "translation"]
    assert evs[0].text == "Hello everyone, welcome to the demo."
    assert evs[1].lang == "es"
    assert fake_translator.calls == [("Hello everyone, welcome to the demo.", "es")]
    assert len(seg.segments) == 1
    assert seg.segments[0].end_ms == 300
    assert evs[1].is_final is True


async def test_target_equal_to_source_is_skipped():
    seg = Segmenter("s1", "en", ["es", "en"], FakeTranslator(), Metrics())
    evs = await seg.feed(final("A final segment.", 1), 100)
    # en se filtra porque == source_lang
    assert [e.kind for e in evs] == ["final", "translation"]


async def test_multiple_times_in_successive_segments(fake_translator):
    seg = Segmenter("s1", "en", ["es"], fake_translator, Metrics())
    await seg.feed(final("First.", 1, start_ms=100), 100)
    await seg.feed(final("Second.", 2, start_ms=200), 200)
    await seg.feed(final("Third.", 3, start_ms=300), 300)
    assert len(seg.segments) == 3
    assert [s.source_text for s in seg.segments] == ["First.", "Second.", "Third."]
    assert seg.segments[0].start_ms == 100
    assert seg.segments[2].audio_pos_ms_at_close == 300


async def test_segment_start_matches_first_partial():
    seg = Segmenter("s1", "en", ["es"], FakeTranslator(), Metrics())
    await seg.feed(partial("Working on", 1), 100)
    await seg.feed(partial("Working on the segment.", 2), 200)
    evs = await seg.feed(final("Working on the segment.", 3), 300)
    assert evs[1].start_ms == 100
    assert evs[1].end_ms == 300