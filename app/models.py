"""Modelos normalizados del pipeline.

Todo lo que cruza los límites internos del sistema usa estos tipos.
El resto de la aplicación NUNCA depende de estructuras específicas de Gemini.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass(slots=True)
class TranscriptEvent:
    """Evento de transcripción normalizado, emitido por un Transcriber."""

    text: str
    is_final: bool
    start_ms: int | None = None
    end_ms: int | None = None
    lang: str | None = None
    # Diagnóstico: cuándo se recibió y en qué punto del audio estaba el pipeline.
    received_at: float | None = None          # monotonic seconds
    audio_pos_ms: int | None = None           # proxy del punto de audio procesado


@dataclass(slots=True)
class Segment:
    """Segmento cerrado listo para traducir y publicar."""

    seq: int
    start_ms: int | None
    end_ms: int | None
    source_text: str
    segment_final_at: float                      # monotonic seconds
    audio_pos_ms_at_close: int                   # proxy del audio en el cierre
    translations: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class WireEvent:
    """Evento publicado al Hub y consumido por SSE/frontend."""

    session_id: str
    kind: Literal["partial", "final", "translation"]
    lang: str
    text: str
    is_final: bool
    start_ms: int | None
    end_ms: int | None
    published_at: float = field(default_factory=time.monotonic)
    seq: int = 0

    def to_sse(self) -> str:
        payload = {
            "seq": self.seq,
            "session_id": self.session_id,
            "kind": self.kind,
            "lang": self.lang,
            "text": self.text,
            "is_final": self.is_final,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }
        data = json.dumps(payload, ensure_ascii=False)
        return f"id: {self.seq}\ndata: {data}\n\n"

    def to_dict(self) -> dict:
        return asdict(self)