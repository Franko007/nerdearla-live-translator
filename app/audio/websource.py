"""Fuente de audio alimentada desde un WebSocket (mic del navegador).

El navegador manda PCM s16le 16 kHz mono binario; esto lo convierte en un
AsyncIterator[bytes] con la misma forma que FfmpegSource, así el resto del
pipeline no cambia.

El cierre se propaga por un asyncio.Event (no por un centinela en la cola)
para que incluso con varios consumidores todos terminen cuando se cierra.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

_END = object()


class WebSource:
    """Cola de chunks de audio. close() termina la iteración y no-op tras eso."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed = False
        self._end_event = asyncio.Event()

    @property
    def closed(self) -> bool:
        return self._closed

    def feed(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return
        self._queue.put_nowait(chunk)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._end_event.set()

    async def _pop(self) -> bytes | object:
        while True:
            if self._queue.empty() and self._closed:
                return _END
            get_task = asyncio.create_task(self._queue.get())
            end_task = asyncio.create_task(self._end_event.wait())
            done, pending = await asyncio.wait(
                {get_task, end_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if get_task in done:
                try:
                    return get_task.result()
                except asyncio.CancelledError:
                    pass
            if not self._queue.empty():
                continue  # drena lo que quedó al cerrarse
            return _END

    async def chunks(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._pop()
            if item is _END:
                return
            yield item