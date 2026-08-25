"""Tests for the reminder scheduler service."""

import asyncio
from datetime import datetime, timedelta

import pytest

from iris.app.database.database import init_db
from iris.app.services.scheduler import SchedulerService, _next_occurrence, parse_at_time


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


def test_parse_at_time_today_or_tomorrow():
    base = datetime(2026, 8, 25, 10, 0)
    later = parse_at_time("15:30", base=base)
    assert later == datetime(2026, 8, 25, 15, 30)
    earlier = parse_at_time("09:00", base=base)
    assert earlier == datetime(2026, 8, 26, 9, 0)  # rolls to tomorrow


def test_parse_at_time_invalid():
    with pytest.raises(ValueError):
        parse_at_time("25:99")
    with pytest.raises(ValueError):
        parse_at_time("later")


def test_next_occurrence_daily():
    past = datetime.now() - timedelta(days=3, hours=2)
    nxt = _next_occurrence(past, "daily")
    assert nxt > datetime.now()
    assert nxt.hour == past.hour and nxt.minute == past.minute


def test_next_occurrence_weekdays_skips_weekend():
    nxt = _next_occurrence(datetime.now() - timedelta(days=1), "weekdays")
    assert nxt.weekday() < 5


async def test_add_list_cancel():
    service = SchedulerService()
    record = await service.add("water the plants", datetime.now() + timedelta(hours=1))
    assert record["status"] == "scheduled"

    items = await service.list_scheduled()
    ids = [i["id"] for i in items]
    assert record["id"] in ids

    assert await service.cancel(record["id"]) is True
    assert await service.cancel(record["id"]) is False  # already cancelled

    items = await service.list_scheduled()
    assert record["id"] not in [i["id"] for i in items]


async def test_due_items_fire_and_complete():
    from iris.app.core.bus import default_event_bus

    service = SchedulerService()
    record = await service.add("fire fast", datetime.now() - timedelta(seconds=1))
    sub = default_event_bus.subscribe(["reminder.due"])
    await service._fire_due()
    remaining = [i["id"] for i in await service.list_scheduled()]
    assert record["id"] not in remaining
    # Bus received the firing.
    fired = None
    while sub.queue.qsize():
        ev = sub.queue.get_nowait()
        if ev.payload.get("id") == record["id"]:
            fired = ev
    assert fired is not None
    default_event_bus.unsubscribe(sub)


async def test_recurring_reschedules_instead_of_completing():
    service = SchedulerService()
    record = await service.add(
        "daily standup", datetime.now() - timedelta(seconds=5), recurrence="daily"
    )
    await service._fire_due()
    items = await service.list_scheduled()
    match = [i for i in items if i["id"] == record["id"]]
    assert match, "recurring item disappeared"
    assert datetime.fromisoformat(match[0]["due_at"]) > datetime.now()
    await service.cancel(record["id"])
