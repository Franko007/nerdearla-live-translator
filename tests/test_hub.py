"""Tests del Hub: seq, buffer, reconexión (Last-Event-ID)."""
from __future__ import annotations

import asyncio

import pytest

from app.hub import Hub
from app.models import WireEvent


def wire(sid: str, kind: str, lang: str, text: str, seq: int = 0) -> WireEvent:
    return WireEvent(
        session_id=sid, kind=kind, lang=lang, text=text,
        is_final=kind != "partial", start_ms=0, end_ms=0,
    )


@pytest.fixture
def hub() -> Hub:
    return Hub(buffer_size=5)


async def test_seq_is_incremental_per_session(hub):
    await hub.publish("a", wire("a", "final", "es", "uno"))
    await hub.publish("a", wire("a", "final", "es", "dos"))
    await hub.publish("b", wire("b", "final", "es", "otro"))
    assert hub.last_seq("a") == 2
    assert hub.last_seq("b") == 1


async def test_events_since_returns_only_newer(hub):
    for i in range(5):
        await hub.publish("a", wire("a", "final", "es", f"t{i}"))
    since = hub.events_since("a", 2)
    assert [e.seq for e in since] == [3, 4, 5]
    assert [e.text for e in since] == ["t2", "t3", "t4"]


async def test_subscriber_receives_live_events(hub):
    received = []

    async def consume():
        async with hub.subscriber("a") as q:
            for _ in range(3):
                received.append((await q.get()).seq)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    for i in range(3):
        await hub.publish("a", wire("a", "final", "es", f"t{i}"))
    await asyncio.wait_for(task, timeout=2)
    assert received == [1, 2, 3]


async def test_buffer_is_bounded(hub):
    for i in range(12):
        await hub.publish("a", wire("a", "final", "es", f"t{i}"))
    assert len(hub._buffers["a"]) == 5
    assert hub.events_since("a", 0)[0].seq == 8


async def test_subscriber_removed_after_close(hub):
    async def consume():
        async with hub.subscriber("a") as q:
            await q.get()

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await hub.publish("a", wire("a", "final", "es", "t0"))
    await asyncio.wait_for(task, timeout=2)
    assert hub._subs["a"] == set()