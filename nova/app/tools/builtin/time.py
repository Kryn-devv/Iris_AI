"""Time tool for retrieving current system time and timestamp info."""

from datetime import datetime, timezone
import time
from typing import Any, Dict

from nova.app.tools.base import BaseTool
from nova.app.core.security import PermissionLevel
from nova.app.schemas.tools import ToolParameterSchema


class TimeTool(BaseTool):
    """Tool for retrieving current system time information."""

    name = "time"
    description = "Returns current local system time, UTC timestamp, time zone, and epoch time."
    permission_level = PermissionLevel.READ
    input_schema = ToolParameterSchema(
        type="object",
        properties={},
        required=[],
    )

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)
        tz_name = time.tzname[time.daylight] if time.daylight else time.tzname[0]

        return {
            "local_time": now_local.strftime("%Y-%m-%d %H:%M:%S"),
            "local_iso": now_local.isoformat(),
            "utc_iso": now_utc.isoformat(),
            "timezone": tz_name,
            "unix_timestamp": time.time(),
        }
