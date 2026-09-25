"""Carga y guardado de grabaciones de replay.

El archivo contiene eventos normalizados (NO respuestas crudas de Gemini):
   - eventos de transcripción con tiempos de reproducción (at_ms, audio-relative)
   - segmentos cerrados con sus traducciones por idioma

El Replay recorre exactamente el mismo pipeline que Gemini: el audio se
reproduce con FfmpegSource y los eventos se emiten a ritmo de wall-clock.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.models import Segment


@dataclass(slots=True)
class ReplayEvent:
    at_ms: int
    kind: str           # "partial" | "final"
    text: str
    start_ms: int | None = None
    end_ms: int | None = None

    def to_dict(self) -> dict:
        return {
            "at_ms": self.at_ms,
            "kind": self.kind,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReplayEvent":
        return cls(
            at_ms=int(d["at_ms"]),
            kind=d["kind"],
            text=d["text"],
            start_ms=d.get("start_ms"),
            end_ms=d.get("end_ms"),
        )


@dataclass(slots=True)
class ReplayData:
    session_id: str
    title: str
    source_lang: str
    target_langs: list[str] = field(default_factory=list)
    audio: str = ""
    events: list[ReplayEvent] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)

    @property
    def translations(self) -> dict[tuple[str, str], str]:
        return {
            (seg.source_text, lang): text
            for seg in self.segments
            for lang, text in seg.translations.items()
        }

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "session_id": self.session_id,
            "title": self.title,
            "source_lang": self.source_lang,
            "target_langs": self.target_langs,
            "audio": self.audio,
            "events": [e.to_dict() for e in self.events],
            "segments": [
                {
                    "seq": seg.seq,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "source_text": seg.source_text,
                    "translations": seg.translations,
                }
                for seg in self.segments
            ],
        }

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_replay(path: Path) -> ReplayData:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ReplayData(
        session_id=data["session_id"],
        title=data.get("title", data["session_id"]),
        source_lang=data.get("source_lang", "en"),
        target_langs=list(data.get("target_langs", [])),
        audio=data.get("audio", ""),
        events=[ReplayEvent.from_dict(e) for e in data.get("events", [])],
        segments=[
            Segment(
                seq=s.get("seq", i + 1),
                start_ms=s.get("start_ms"),
                end_ms=s.get("end_ms"),
                source_text=s["source_text"],
                translations=s.get("translations", {}),
                segment_final_at=time.monotonic(),
                audio_pos_ms_at_close=s.get("end_ms") or 0,
            )
            for i, s in enumerate(data.get("segments", []))
        ],
    )


def replay_path_for_audio(audio_path: str | Path) -> Path:
    p = Path(audio_path)
    return p.with_suffix(".replay.json")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "session"