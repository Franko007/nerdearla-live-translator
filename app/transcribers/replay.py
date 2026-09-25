"""Transcripción mediante una grabación normalizada (modo Replay).

Emite los eventos guardados a ritmo de wall-clock respetando sus tiempos,
mientras el audio avanza en paralelo para mantener el mismo pipeline
que en una ejecución real (y latencias realistas).
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from app.models import TranscriptEvent
from app.replay import ReplayData


class ReplayTranscriber:
    def __init__(self, data: ReplayData) -> None:
        self.data = data
        self._drain: asyncio.Task | None = None

    async def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[TranscriptEvent]:
        # Consumir el audio en background para que ffmpeg no bloquee la cañería.
        self._drain = asyncio.create_task(self._drain_audio(audio))
        start = time.monotonic()
        try:
            for rec in self.data.events:
                remaining = (start + rec.at_ms / 1000) - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                yield TranscriptEvent(
                    text=rec.text,
                    is_final=rec.kind == "final",
                    start_ms=rec.start_ms,
                    end_ms=rec.end_ms,
                    received_at=time.monotonic(),
                )
        finally:
            if self._drain and not self._drain.done():
                self._drain.cancel()

    async def _drain_audio(self, audio: AsyncIterator[bytes]) -> None:
        async for _ in audio:
            pass

    async def close(self) -> None:
        if self._drain and not self._drain.done():
            self._drain.cancel()