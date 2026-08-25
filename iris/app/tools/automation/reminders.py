"""Reminder, timer and routine tools backed by the scheduler service."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.services.scheduler import default_scheduler_service, parse_at_time
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.automation")

_MAX_AHEAD_DAYS = 366


def _humanize_due(due_at: datetime) -> str:
    """Short natural phrase for when something fires."""
    delta = due_at - datetime.now()
    total = int(delta.total_seconds())
    if total < 90:
        return f"in {max(total, 1)} seconds"
    if total < 5400:
        return f"in {round(total / 60)} minutes"
    if due_at.date() == datetime.now().date():
        return f"today at {due_at.strftime('%H:%M')}"
    if due_at.date() == (datetime.now() + timedelta(days=1)).date():
        return f"tomorrow at {due_at.strftime('%H:%M')}"
    return due_at.strftime("on %A at %H:%M")


class SetReminderTool(BaseTool):
    """Schedule a one-off or recurring reminder."""

    name = "set_reminder"
    description = "Set a reminder that fires at a given time or after a delay, with optional daily/weekly recurrence."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.AUTOMATION
    aliases = ("remind_me", "add_reminder", "create_reminder", "set_alarm")
    mutating = True
    examples = (
        ToolExample(utterance="remind me in 10 minutes to stretch", arguments={"text": "stretch", "in_seconds": 600}),
        ToolExample(utterance="remind me to call mom at 5 pm", arguments={"text": "call mom", "at_time": "17:00"}),
        ToolExample(utterance="remind me every day at 9 to journal", arguments={"text": "journal", "at_time": "09:00", "recurrence": "daily"}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "text": {"type": "string", "description": "What to remind about."},
            "in_seconds": {"type": "integer", "description": "Fire after this many seconds."},
            "at_time": {"type": "string", "description": "Fire at HH:MM (24h), today or tomorrow."},
            "at_iso": {"type": "string", "description": "Fire at an exact ISO datetime."},
            "recurrence": {
                "type": "string",
                "enum": ["hourly", "daily", "weekly", "weekdays"],
                "description": "Optional repetition.",
            },
        },
        required=["text"],
    )

    async def _run(
        self,
        text: str,
        in_seconds: int | None = None,
        at_time: str | None = None,
        at_iso: str | None = None,
        recurrence: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        due_at = self._resolve_due(in_seconds=in_seconds, at_time=at_time, at_iso=at_iso)
        record = await default_scheduler_service.add(
            text=text, due_at=due_at, kind="reminder", recurrence=recurrence
        )
        when = _humanize_due(due_at)
        suffix = f", repeating {recurrence}" if recurrence else ""
        return {
            "reminder": record,
            "speech": f"Okay, I'll remind you to {text.strip()} {when}{suffix}.",
        }

    @staticmethod
    def _resolve_due(
        *, in_seconds: int | None, at_time: str | None, at_iso: str | None
    ) -> datetime:
        if in_seconds is not None:
            if in_seconds <= 0:
                raise ToolError("The delay must be positive.")
            if in_seconds > _MAX_AHEAD_DAYS * 86400:
                raise ToolError("That's too far in the future.")
            return datetime.now() + timedelta(seconds=int(in_seconds))
        if at_time:
            try:
                return parse_at_time(at_time)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
        if at_iso:
            try:
                due = datetime.fromisoformat(at_iso)
            except ValueError as exc:
                raise ToolError(f"Invalid datetime {at_iso!r}.") from exc
            if due <= datetime.now():
                raise ToolError("That time is in the past.")
            return due
        raise ToolError("Tell me when: give in_seconds, at_time (HH:MM) or at_iso.")


class SetTimerTool(BaseTool):
    """Start a countdown timer."""

    name = "set_timer"
    description = "Start a countdown timer for a number of seconds with an optional label."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.AUTOMATION
    aliases = ("timer", "start_timer", "countdown")
    mutating = True
    examples = (
        ToolExample(utterance="set a timer for 5 minutes", arguments={"seconds": 300}),
        ToolExample(utterance="timer 30 seconds for tea", arguments={"seconds": 30, "label": "tea"}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "seconds": {"type": "integer", "description": "Countdown length in seconds."},
            "label": {"type": "string", "description": "Optional timer label."},
        },
        required=["seconds"],
    )

    async def _run(self, seconds: int, label: str | None = None, **_: Any) -> dict[str, Any]:
        seconds = int(seconds)
        if seconds <= 0:
            raise ToolError("Timer length must be positive.")
        if seconds > 86400:
            raise ToolError("Timers max out at 24 hours — use a reminder instead.")
        text = (label or "Timer").strip()
        due_at = datetime.now() + timedelta(seconds=seconds)
        record = await default_scheduler_service.add(text=text, due_at=due_at, kind="timer")
        minutes, secs = divmod(seconds, 60)
        pretty = f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes else f"{secs} seconds"
        if minutes and secs:
            pretty = f"{minutes}m {secs}s"
        return {"timer": record, "speech": f"Timer set for {pretty}."}


class ListRemindersTool(BaseTool):
    """Show scheduled reminders and timers."""

    name = "list_reminders"
    description = "List upcoming reminders, timers and routines."
    permission_level = PermissionLevel.READ
    category = ToolCategory.AUTOMATION
    aliases = ("show_reminders", "my_reminders", "list_timers")
    examples = (ToolExample(utterance="what are my reminders", arguments={}),)

    async def _run(self, **_: Any) -> dict[str, Any]:
        items = await default_scheduler_service.list_scheduled()
        if not items:
            return {"reminders": [], "speech": "You have no reminders scheduled."}
        lines = []
        for item in items:
            due = datetime.fromisoformat(item["due_at"]) if item["due_at"] else None
            when = _humanize_due(due) if due else "sometime"
            rec = f" (repeats {item['recurrence']})" if item.get("recurrence") else ""
            lines.append(f"• {item['text']} — {when}{rec}  [{item['id']}]")
        return {
            "reminders": items,
            "display": "\n".join(lines),
            "speech": f"You have {len(items)} reminder{'s' if len(items) != 1 else ''} scheduled.",
        }


class CancelReminderTool(BaseTool):
    """Cancel a scheduled reminder or timer."""

    name = "cancel_reminder"
    description = "Cancel a reminder, timer or routine by its id, or the next upcoming one."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.AUTOMATION
    aliases = ("delete_reminder", "remove_reminder", "cancel_timer")
    mutating = True
    input_schema = ToolParameterSchema(
        properties={"reminder_id": {"type": "string", "description": "Reminder id; omit to cancel the next one."}},
    )

    async def _run(self, reminder_id: str | None = None, **_: Any) -> dict[str, Any]:
        if not reminder_id:
            items = await default_scheduler_service.list_scheduled(limit=1)
            if not items:
                raise ToolError("There's nothing scheduled to cancel.")
            reminder_id = items[0]["id"]
        ok = await default_scheduler_service.cancel(reminder_id)
        if not ok:
            raise ToolError(f"I couldn't find an active reminder with id {reminder_id}.")
        return {"cancelled": reminder_id, "speech": "Cancelled."}


class SetRoutineTool(BaseTool):
    """Create a recurring routine announcement."""

    name = "set_routine"
    description = "Create a recurring routine (daily/weekdays/weekly/hourly) announced at a set time."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.AUTOMATION
    aliases = ("add_routine", "create_routine", "daily_reminder")
    mutating = True
    examples = (
        ToolExample(
            utterance="every weekday at 9 remind me to check email",
            arguments={"text": "check email", "at_time": "09:00", "recurrence": "weekdays"},
        ),
    )
    input_schema = ToolParameterSchema(
        properties={
            "text": {"type": "string"},
            "at_time": {"type": "string", "description": "HH:MM 24h"},
            "recurrence": {"type": "string", "enum": ["hourly", "daily", "weekly", "weekdays"]},
        },
        required=["text", "at_time", "recurrence"],
    )

    async def _run(self, text: str, at_time: str, recurrence: str, **_: Any) -> dict[str, Any]:
        try:
            due_at = parse_at_time(at_time)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        record = await default_scheduler_service.add(
            text=text, due_at=due_at, kind="routine", recurrence=recurrence
        )
        return {
            "routine": record,
            "speech": f"Routine created: {text}, {recurrence} at {at_time}.",
        }


def get_tools() -> list[BaseTool]:
    return [
        SetReminderTool(),
        SetTimerTool(),
        ListRemindersTool(),
        CancelReminderTool(),
        SetRoutineTool(),
    ]
