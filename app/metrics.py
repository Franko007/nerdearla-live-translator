"""Métricas por sesión.

Se guardan internamente los timestamps necesarios para diagnosticar el
pipeline; el panel/admin sólo muestran agregados (p50/p95).

Definiciones (limitaciones documentadas en README):
  - transcription latency (proxy): cuánto "detrás del audio" llegó el segmento
    final = segment_final_at - audio_pos_at_close.
  - translation latency: tiempo de la llamada de traducción.
  - pipeline latency (proxy): cuándo quedó publicado el segmento traducido
    relativo al punto de audio que lo originó.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


@dataclass(slots=True)
class Metrics:
    transcription_ms: list[float] = field(default_factory=list)
    translation_ms: list[float] = field(default_factory=list)
    pipeline_ms: list[float] = field(default_factory=list)
    errors: int = 0
    reconnections: int = 0
    reconnections_exposed: int = 0
    audio_ms: int = 0
    translation_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    def snapshot(self) -> dict:
        return {
            "transcription_latency_ms": self._pretty(self.transcription_ms),
            "translation_latency_ms": self._pretty(self.translation_ms),
            "pipeline_latency_ms": self._pretty(self.pipeline_ms),
            "p50_ms": {
                "transcription": _round(self.transcription_ms, 50),
                "translation": _round(self.translation_ms, 50),
                "pipeline": _round(self.pipeline_ms, 50),
            },
            "p95_ms": {
                "transcription": _round(self.transcription_ms, 95),
                "translation": _round(self.translation_ms, 95),
                "pipeline": _round(self.pipeline_ms, 95),
            },
            "errors": self.errors,
            "reconnections": self.reconnections,
            "audio_seconds": round(self.audio_ms / 1000, 1),
            "translation_calls": self.translation_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }

    @staticmethod
    def _pretty(values: list[float]) -> dict:
        return {"p50_ms": _round(values, 50), "p95_ms": _round(values, 95), "n": len(values)}


def _round(values: list[float], p: int) -> float:
    v = pct(values, p / 100)
    return round(v, 1) if v == v else None