"""Tests del modo Replay: roundtrip, traducciones y eventos."""
from __future__ import annotations

from pathlib import Path

from app.models import Segment
from app.replay import ReplayData, load_replay
from app.translators.replay import ReplayTranslator


def sample_data() -> ReplayData:
    return ReplayData(
        session_id="talk-a",
        title="Talk A",
        source_lang="en",
        target_langs=["es"],
        audio="samples/talk-a.wav",
        events=[],
        segments=[
            Segment(
                seq=1,
                start_ms=100,
                end_ms=900,
                source_text="Hello there.",
                translations={"es": "Hola."},
                segment_final_at=0.1,
                audio_pos_ms_at_close=900,
            )
        ],
    )


def test_roundtrip(tmp_path: Path):
    path = tmp_path / "info.replay.json"
    sample_data().save(path)
    loaded = load_replay(path)
    assert loaded.session_id == "talk-a"
    assert loaded.title == "Talk A"
    assert len(loaded.segments) == 1
    assert loaded.segments[0].translations == {"es": "Hola."}


def test_translator_resolves_by_pair():
    tr = ReplayTranslator(sample_data())
    assert tr.calls == 0
    out = __import__("asyncio").run(tr.translate("Hello there.", "es"))
    assert out == "Hola."
    assert tr.hits == 1


def test_translator_fallback_to_source():
    tr = ReplayTranslator(sample_data())
    out = __import__("asyncio").run(tr.translate("Unknown sentence.", "es"))
    assert out == "Unknown sentence."


def test_translations_map():
    data = sample_data()
    assert data.translations[("Hello there.", "es")] == "Hola."