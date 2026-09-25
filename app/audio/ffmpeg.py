"""Fuente de audio FFmpeg: lee un archivo o URL y produce PCM 16 kHz / 16-bit / mono.

Mantiene el ritmo real de reproducción para poder medir latencia.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from typing import AsyncIterator

SAMPLE_RATE = 16000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000   # 32 bytes por ms
CHUNK_MS = 100
CHUNK_BYTES = BYTES_PER_MS * CHUNK_MS   # 3200 bytes


class FfmpegSource:
    def __init__(self, source: str, chunk_ms: int = CHUNK_MS, rate: int = SAMPLE_RATE) -> None:
        if shutil.which("ffmpeg") is None:
            sys.exit("ffmpeg no está instalado o no está en el PATH.")
        self.source = source
        self.chunk_ms = chunk_ms
        self.rate = rate
        self.bytes_per_ms = rate * 2 // 1000
        self._proc: asyncio.subprocess.Process | None = None
        self._started = 0.0

    def __repr__(self) -> str:
        return f"FfmpegSource({self.source!r})"

    async def _spawn(self) -> asyncio.subprocess.Process:
        cmd = [
            "ffmpeg", "-loglevel", "error", "-i", self.source,
            "-f", "s16le", "-ac", "1", "-ar", str(self.rate), "-",
        ]
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def chunks(self) -> AsyncIterator[bytes]:
        """Genera chunks de audio manteniendo el ritmo de reproducción real."""
        self._proc = await self._spawn()
        assert self._proc.stdout is not None
        self._started = time.monotonic()
        sent = 0
        try:
            while True:
                chunk = await self._proc.stdout.read(self.chunk_ms * self.bytes_per_ms)
                if not chunk:
                    break
                yield chunk
                sent += len(chunk)
                target = self._started + sent / (self.rate * 2)
                delay = target - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
        finally:
            stderr = await self._proc.stderr.read()
            if self._proc.returncode not in (0, None):
                text = stderr.decode(errors="ignore").strip()
                if text:
                    raise RuntimeError(f"ffmpeg falló con {self.source}: {text}")
            if self._proc.returncode is None:
                self._proc.kill()

    async def close(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()