"""Segmenter: convierte parciales/finales en segmentos lógicos cerrados.

- Los parciales se emiten a la audiencia, pero NO se traducen.
- La traducción se dispara únicamente cuando el segmento está cerrado (final).
- Un segmento cerrado produce: 1 evento final (original) + N eventos de
  traducción (uno por idioma destino distinto al origen).
"""
from __future__ import annotations

import time

from app.metrics import Metrics
from app.models import Segment, TranscriptEvent, WireEvent
from app.translators.base import Translator


class Segmenter:
    def __init__(
        self,
        session_id: str,
        source_lang: str,
        target_langs: list[str],
        translator: Translator,
        metrics: Metrics,
        origin_s: float = 0.0,
    ) -> None:
        self.session_id = session_id
        self.source_lang = source_lang
        self.target_langs = [t for t in target_langs if t != source_lang]
        self.translator = translator
        self.metrics = metrics
        self.origin_s = origin_s
        self.segments: list[Segment] = []
        self._seg_open = False
        self._seg_start_ms: int | None = None
        self._next_seq = 1

    async def feed(self, ev: TranscriptEvent, audio_pos_ms: int | None) -> list[WireEvent]:
        """Procesa un evento de transcripción y devuelve los WireEvents a publicar."""
        out: list[WireEvent] = []

        if not ev.is_final:
            if not self._seg_open:
                self._seg_open = True
                self._seg_start_ms = ev.start_ms if ev.start_ms is not None else audio_pos_ms
            out.append(self._wire("partial", self.source_lang, ev.text, False, ev.start_ms, ev.end_ms))
            return out

        # --- Cierre de segmento ---
        self._seg_open = False
        start_ms = self._seg_start_ms if self._seg_start_ms is not None else (ev.start_ms or ev.end_ms)
        end_ms = ev.end_ms if ev.end_ms is not None else audio_pos_ms
        final_at = ev.received_at if ev.received_at is not None else time.monotonic()

        seg = Segment(
            seq=self._next_seq,
            start_ms=start_ms,
            end_ms=end_ms,
            source_text=ev.text,
            segment_final_at=final_at,
            audio_pos_ms_at_close=audio_pos_ms or end_ms or 0,
        )
        self._next_seq += 1
        self.segments.append(seg)

        out.append(self._wire("final", self.source_lang, ev.text, True, start_ms, end_ms))

        for target in self.target_langs:
            t0 = time.monotonic()
            translated = await self.translator.translate(seg.source_text, target)
            dt_ms = (time.monotonic() - t0) * 1000
            seg.translations[target] = translated
            self.metrics.translation_ms.append(dt_ms)
            self.metrics.translation_calls += 1
            if seg.audio_pos_ms_at_close:
                wall_at_close = self.origin_s + seg.audio_pos_ms_at_close / 1000
                self.metrics.pipeline_ms.append((time.monotonic() - wall_at_close) * 1000)

        # Proxy de latencia de transcripción: recibido vs. punto de audio procesado.
        if seg.audio_pos_ms_at_close:
            wall_at_close = self.origin_s + seg.audio_pos_ms_at_close / 1000
            self.metrics.transcription_ms.append((final_at - wall_at_close) * 1000)

        return out

    def _wire(self, kind: str, lang: str, text: str, is_final: bool, start_ms, end_ms) -> WireEvent:
        return WireEvent(
            session_id=self.session_id,
            kind=kind,
            lang=lang,
            text=text,
            is_final=is_final,
            start_ms=start_ms,
            end_ms=end_ms,
        )