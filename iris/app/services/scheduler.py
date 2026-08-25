"""Reminder, timer and routine scheduler for IRIS.

An asyncio loop watches the persistent ``reminders`` table and fires each item
at its due time: publishing a bus event (which the web UI, voice pipeline and
Telegram bridge each turn into their own announcement), then rescheduling
recurring routines. Persistence means reminders survive restarts — IRIS is a
startup app, so a reminder set today fires tomorrow even after a reboot.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, time as dtime, timedelta
from typing import Any, Optional

from sqlalchemy import select

from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.logging import get_logger
from iris.app.database.database import AsyncSessionLocal
from iris.app.database.models import ReminderModel

logger = get_logger("services.scheduler")

_POLL_SECONDS = 1.0
_RECURRENCE_STEPS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "weekdays": timedelta(days=1),  # step then skip weekends in _next_occurrence
}


def _next_occurrence(due_at: datetime, recurrence: str) -> Optional[datetime]:
    """Next due time strictly in the future for a recurring item."""
    step = _RECURRENCE_STEPS.get(recurrence)
    if step is None:
        return None
    now = datetime.now()
    nxt = due_at
    while nxt <= now:
        nxt = nxt + step
    if recurrence == "weekdays":
        while nxt.weekday() >= 5:  # 5=Sat, 6=Sun
            nxt = nxt + timedelta(days=1)
    return nxt


def parse_at_time(at_time: str, *, base: Optional[datetime] = None) -> datetime:
    """Parse "HH:MM" into the next matching datetime (today or tomorrow)."""
    base = base or datetime.now()
    try:
        hour_s, minute_s = at_time.strip().split(":", 1)
        target_t = dtime(int(hour_s), int(minute_s))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid time {at_time!r}; expected HH:MM.") from exc
    candidate = base.replace(hour=target_t.hour, minute=target_t.minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate


class SchedulerService:
    """Async reminder/timer/routine scheduler backed by SQLite."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()
        self.running = False

    # ---------------------------------------------------------------- control
    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="iris-scheduler")
        self.running = True
        logger.info("Scheduler service started.")

    async def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.running = False
        logger.info("Scheduler service stopped.")

    # ------------------------------------------------------------------- CRUD
    async def add(
        self,
        text: str,
        due_at: datetime,
        *,
        kind: str = "reminder",
        recurrence: Optional[str] = None,
        channel: str = "all",
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Persist a new scheduled item and return its record."""
        if recurrence and recurrence not in _RECURRENCE_STEPS:
            raise ValueError(f"Unsupported recurrence {recurrence!r}.")
        item = ReminderModel(
            id=f"rem_{uuid.uuid4().hex[:12]}",
            kind=kind,
            text=text.strip() or "Reminder",
            due_at=due_at,
            recurrence=recurrence,
            status="scheduled",
            channel=channel,
            meta_json=json.dumps(metadata) if metadata else None,
        )
        async with AsyncSessionLocal() as session:
            session.add(item)
            await session.commit()
        logger.info("Scheduled %s %s at %s (recurrence=%s)", kind, item.id, due_at, recurrence)
        return self._to_dict(item)

    async def cancel(self, reminder_id: str) -> bool:
        async with AsyncSessionLocal() as session:
            item = await session.get(ReminderModel, reminder_id)
            if item is None or item.status != "scheduled":
                return False
            item.status = "cancelled"
            await session.commit()
        return True

    async def list_scheduled(self, *, include_done: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            stmt = select(ReminderModel).order_by(ReminderModel.due_at.asc()).limit(limit)
            if not include_done:
                stmt = stmt.where(ReminderModel.status == "scheduled")
            rows = (await session.execute(stmt)).scalars().all()
        return [self._to_dict(r) for r in rows]

    # ------------------------------------------------------------------- loop
    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._fire_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                logger.error("Scheduler tick failed: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_POLL_SECONDS)
            except asyncio.TimeoutError:
                continue

    async def _fire_due(self) -> None:
        now = datetime.now()
        async with AsyncSessionLocal() as session:
            stmt = (
                select(ReminderModel)
                .where(ReminderModel.status == "scheduled", ReminderModel.due_at <= now)
                .order_by(ReminderModel.due_at.asc())
                .limit(20)
            )
            due_items = (await session.execute(stmt)).scalars().all()

            for item in due_items:
                if item.recurrence:
                    nxt = _next_occurrence(item.due_at, item.recurrence)
                    if nxt is not None:
                        item.due_at = nxt
                    else:
                        item.status = "fired"
                else:
                    item.status = "fired"
                item.fired_at = now
            if due_items:
                await session.commit()

        for item in due_items:
            payload = self._to_dict(item)
            logger.info("Firing %s %s: %s", item.kind, item.id, item.text)
            topic = Topics.REMINDER_DUE if item.kind != "routine" else Topics.ROUTINE_FIRED
            default_event_bus.publish(topic, payload)
            await self._announce(payload)

    async def _announce(self, payload: dict[str, Any]) -> None:
        """Best-effort desktop notification + spoken announcement."""
        title = "⏰ Timer" if payload.get("kind") == "timer" else "🔔 Reminder"
        text = payload.get("text") or ""
        try:
            from iris.app.tools.registry import default_tool_registry

            notify = default_tool_registry.get("notify")
            if notify is not None and notify.is_available():
                await notify.execute(title=title, message=text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Reminder notification failed: %s", exc)

        try:
            from iris.app.voice.service import default_voice_service

            if default_voice_service is not None:
                await default_voice_service.speak(f"{'Timer done' if payload.get('kind') == 'timer' else 'Reminder'}: {text}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Reminder speech failed: %s", exc)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _to_dict(item: ReminderModel) -> dict[str, Any]:
        return {
            "id": item.id,
            "kind": item.kind,
            "text": item.text,
            "due_at": item.due_at.isoformat() if item.due_at else None,
            "recurrence": item.recurrence,
            "status": item.status,
            "channel": item.channel,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }


default_scheduler_service = SchedulerService()
