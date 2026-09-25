"""Hub: pub/sub en memoria con buffer limitado por sesión.

Cada evento publicado recibe un seq incremental por sesión. El buffer permite
reconexiones SSE con Last-Event-ID sin depender de Redis.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from app.models import WireEvent


class Hub:
    def __init__(self, buffer_size: int = 300) -> None:
        self._buffer_size = buffer_size
        self._buffers: dict[str, deque[WireEvent]] = defaultdict(
            lambda: deque(maxlen=buffer_size)
        )
        self._seq: dict[str, int] = defaultdict(int)
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, ev: WireEvent) -> None:
        async with self._lock:
            self._seq[session_id] += 1
            ev.seq = self._seq[session_id]
            self._buffers[session_id].append(ev)
            for q in list(self._subs[session_id]):
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    pass

    def events_since(self, session_id: str, last_seq: int) -> list[WireEvent]:
        return [e for e in self._buffers.get(session_id, ()) if e.seq > last_seq]

    def last_seq(self, session_id: str) -> int:
        buf = self._buffers.get(session_id)
        if not buf:
            return 0
        return buf[-1].seq

    @asynccontextmanager
    async def subscriber(self, session_id: str) -> asyncio.Queue:
        """Suscripción a eventos en vivo. Registra la cola al entrar y la
        elimina al salir (reconexión/disconnect de SSE)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._subs[session_id].add(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subs[session_id].discard(q)