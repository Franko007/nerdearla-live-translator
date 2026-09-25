"""Contrato mínimo de transcripción.

El pipeline sólo conoce esta interfaz; la implementación del proveedor
(Gemini, Replay, ...) queda aislada del resto del sistema.
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol

from app.models import TranscriptEvent


class Transcriber(Protocol):
    async def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[TranscriptEvent]:
        """Consume chunks de audio PCM y produce eventos de transcripción normalizados."""
        ...

    async def close(self) -> None:
        """Libera recursos (desconexión, tareas en background)."""
        ...