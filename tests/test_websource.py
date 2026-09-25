"""Unit tests del WebSource (fuente de audio alimentada por WebSocket)."""
from __future__ import annotations

import pytest

from app.audio.websource import WebSource


async def test_feed_then_yield_in_order():
    ws = WebSource()
    ws.feed(b"aaa")
    ws.feed(b"bbb")
    ws.feed(b"")
    it = ws.chunks()
    assert await anext(it) == b"aaa"
    assert await anext(it) == b"bbb"
    ws.close()
    assert await anext(it, b"") == b""


async def test_close_terminates_iteration():
    ws = WebSource()
    ws.feed(b"x")
    ws.close()
    it = ws.chunks()
    assert await anext(it) == b"x"
    with pytest.raises(StopAsyncIteration):
        await anext(it)
    assert ws.closed


async def test_feed_after_close_is_noop():
    ws = WebSource()
    ws.close()
    ws.feed(b"ignorado")
    it = ws.chunks()
    with pytest.raises(StopAsyncIteration):
        await anext(it)
    assert ws.closed


async def test_two_consumers_share_queue():
    ws = WebSource()
    ws.feed(b"a")
    ws.feed(b"b")
    it1 = ws.chunks()
    it2 = ws.chunks()
    assert await anext(it1) == b"a"
    assert await anext(it2) == b"b"
    ws.close()
    assert await anext(it1, b"") == b""
    assert await anext(it2, b"") == b""