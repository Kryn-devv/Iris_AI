"""Time tool for retrieving current system time and timestamp info."""

from datetime import datetime, timezone
import time
from typing import Any, Dict

from iris.app.tools.base import BaseTool
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema


class TimeTool(BaseTool):
    """Tool for retrieving current system time information."""

    name = "time"
    description = "Returns current local system time, date, UTC timestamp, time zone, and epoch time."
    permission_level = PermissionLevel.READ
    category = ToolCategory.SYSTEM
    aliases = ("current_time", "clock", "date", "what_time")
    examples = (ToolExample(utterance="what time is it", arguments={}),)
    input_schema = ToolParameterSchema(
        type="object",
        properties={},
        required=[],
    )

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)
        tz_name = time.tzname[time.daylight] if time.daylight else time.tzname[0]

        spoken = now_local.strftime("%I:%M %p").lstrip("0")
        date_spoken = now_local.strftime("%A, %B %d")
        return {
            "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
            "local_iso": now_local.isoformat(),
            "utc_iso": now_utc.isoformat(),
            "timezone": tz_name,
            "unix_timestamp": time.time(),
            "speech": f"It's {spoken} on {date_spoken}.",
            "display": f"🕐 {spoken} — {date_spoken} ({tz_name})",
        }
