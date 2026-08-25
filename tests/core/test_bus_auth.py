"""Tests for the event bus and remote-access auth."""

import asyncio

import pytest

from iris.app.core.auth import RateLimiter, extract_token, is_loopback
from iris.app.core.bus import EventBus, Topics


# ---------------------------------------------------------------------- bus
def test_bus_topic_filtering():
    bus = EventBus()
    agent_sub = bus.subscribe(["agent.*"])
    all_sub = bus.subscribe()
    bus.publish(Topics.AGENT_STARTED, {"a": 1})
    bus.publish(Topics.VOICE_WAKE, {"b": 2})
    assert agent_sub.queue.qsize() == 1
    assert all_sub.queue.qsize() == 2


def test_bus_history_and_limits():
    bus = EventBus(history_size=5)
    for i in range(10):
        bus.publish("t", {"i": i})
    history = bus.history()
    assert len(history) == 5
    assert history[-1].payload["i"] == 9


def test_bus_overflow_drops_oldest():
    bus = EventBus()
    sub = bus.subscribe(maxsize=3)
    for i in range(6):
        bus.publish("t", {"i": i})
    assert sub.queue.qsize() == 3
    assert sub.dropped == 3
    first = sub.queue.get_nowait()
    assert first.payload["i"] == 3  # oldest three dropped


async def test_bus_async_iteration():
    bus = EventBus()
    sub = bus.subscribe(["x"])
    bus.publish("x", {"n": 1})

    async def read_one():
        async for event in sub:
            return event

    event = await asyncio.wait_for(read_one(), timeout=1)
    assert event.payload["n"] == 1


def test_unsubscribe_removes_listener():
    bus = EventBus()
    sub = bus.subscribe()
    bus.unsubscribe(sub)
    bus.publish("t", {})
    assert bus.subscriber_count == 0


# --------------------------------------------------------------------- auth
def test_loopback_detection():
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert is_loopback("testclient")
    assert not is_loopback("192.168.1.20")


def test_extract_token_priority():
    assert extract_token("Bearer abc", None, None) == "abc"
    assert extract_token("token xyz", None, None) == "xyz"
    assert extract_token(None, "hdr", "qry") == "hdr"
    assert extract_token(None, None, "qry") == "qry"
    assert extract_token(None, None, None) is None


def test_rate_limiter():
    limiter = RateLimiter(limit_per_minute=3)
    assert limiter.check("a")
    assert limiter.check("a")
    assert limiter.check("a")
    assert not limiter.check("a")
    assert limiter.check("b")  # other clients unaffected
    limiter.reset("a")
    assert limiter.check("a")
