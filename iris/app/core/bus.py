"""In-process async publish/subscribe bus.

The web UI, tray icon, voice pipeline and Telegram bridge all need to observe
the same stream of agent activity (planning, tool calls, voice state, speech
transcripts). The bus decouples producers from consumers: producers call
:meth:`EventBus.publish` synchronously from anywhere, and each subscriber owns a
bounded queue so one slow WebSocket client can never stall the kernel.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Deque, Optional

from iris.app.core.logging import get_logger

logger = get_logger("core.bus")

DEFAULT_QUEUE_SIZE = 256
DEFAULT_HISTORY_SIZE = 200


@dataclass
class BusEvent:
    """A single broadcast message."""

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


class Subscription:
    """A bounded per-subscriber queue with newest-wins overflow."""

    def __init__(self, topics: Optional[set[str]] = None, maxsize: int = DEFAULT_QUEUE_SIZE):
        self.topics = topics
        self.queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
        self.closed = False

    def matches(self, topic: str) -> bool:
        """True when this subscriber wants ``topic``.

        ``"agent.*"`` style prefix wildcards are supported.
        """
        if not self.topics:
            return True
        for wanted in self.topics:
            if wanted == topic or wanted == "*":
                return True
            if wanted.endswith("*") and topic.startswith(wanted[:-1]):
                return True
        return False

    def offer(self, event: BusEvent) -> None:
        """Enqueue without blocking; drop the oldest item when full."""
        if self.closed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass
            self.dropped += 1

    async def __aiter__(self) -> AsyncIterator[BusEvent]:
        while not self.closed:
            event = await self.queue.get()
            yield event

    def close(self) -> None:
        self.closed = True


class EventBus:
    """Fan-out broadcaster with a replayable recent-history ring buffer."""

    def __init__(self, history_size: int = DEFAULT_HISTORY_SIZE):
        self._subscribers: list[Subscription] = []
        self._history: Deque[BusEvent] = deque(maxlen=history_size)

    # ----------------------------------------------------------- publishing
    def publish(self, topic: str, payload: Optional[dict[str, Any]] = None) -> BusEvent:
        """Publish an event. Safe to call from sync or async code."""
        event = BusEvent(topic=topic, payload=payload or {})
        self._history.append(event)
        for sub in list(self._subscribers):
            if sub.closed:
                self._subscribers.remove(sub)
                continue
            if sub.matches(topic):
                sub.offer(event)
        return event

    # ---------------------------------------------------------- subscribing
    def subscribe(
        self,
        topics: Optional[list[str]] = None,
        maxsize: int = DEFAULT_QUEUE_SIZE,
    ) -> Subscription:
        """Register a new subscriber."""
        sub = Subscription(topics=set(topics) if topics else None, maxsize=maxsize)
        self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.close()
        if sub in self._subscribers:
            self._subscribers.remove(sub)

    # -------------------------------------------------------------- history
    def history(self, topics: Optional[list[str]] = None, limit: int = 50) -> list[BusEvent]:
        """Recent events, newest last, optionally filtered by topic."""
        wanted = Subscription(set(topics) if topics else None)
        items = [e for e in self._history if wanted.matches(e.topic)]
        return items[-limit:]

    @property
    def subscriber_count(self) -> int:
        return len([s for s in self._subscribers if not s.closed])

    def clear(self) -> None:
        self._history.clear()


class Topics:
    """Canonical topic names. Producers and consumers must agree on these."""

    AGENT = "agent"
    AGENT_STARTED = "agent.started"
    AGENT_THINKING = "agent.thinking"
    AGENT_PLAN = "agent.plan"
    AGENT_TOKEN = "agent.token"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_CONFIRM = "tool.confirm_required"

    VOICE_STATE = "voice.state"
    VOICE_WAKE = "voice.wake"
    VOICE_PARTIAL = "voice.partial"
    VOICE_FINAL = "voice.final"
    VOICE_SPEAKING = "voice.speaking"
    VOICE_LEVEL = "voice.level"

    LLM_ROUTE = "llm.route"
    LLM_FALLBACK = "llm.fallback"

    NODE_LINKED = "node.linked"
    NODE_UNLINKED = "node.unlinked"
    NODE_TELEMETRY = "node.telemetry"
    NODE_ALERT = "node.alert"

    SYSTEM_NOTICE = "system.notice"
    SYSTEM_METRICS = "system.metrics"

    ROUTINE_FIRED = "routine.fired"
    REMINDER_DUE = "reminder.due"

    UI_STATE = "ui.state"


default_event_bus = EventBus()
