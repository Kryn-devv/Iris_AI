"""Desktop-shell tools: autostart control."""

from __future__ import annotations

from typing import Any

from iris.app.core.security import PermissionLevel
from iris.app.desktop import autostart
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError


class AutostartTool(BaseTool):
    """Control whether IRIS starts with the computer."""

    name = "autostart"
    description = "Enable, disable or check starting IRIS automatically when the computer boots."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.SYSTEM
    aliases = ("start_with_windows", "start_on_boot", "run_at_startup", "startup_app")
    os_support = ("windows", "linux", "macos")
    mutating = True
    examples = (
        ToolExample(utterance="start iris when my pc boots", arguments={"action": "enable"}),
        ToolExample(utterance="stop starting iris at startup", arguments={"action": "disable"}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "action": {"type": "string", "enum": ["enable", "disable", "status"], "description": "What to do."},
        },
        required=["action"],
    )

    async def _run(self, action: str = "status", **_: Any) -> dict[str, Any]:
        action = (action or "status").lower()
        if action == "enable":
            info = await self.to_thread(autostart.enable)
            return {**info, "speech": "Done — I'll start automatically when your computer boots."}
        if action == "disable":
            info = await self.to_thread(autostart.disable)
            return {**info, "speech": "Okay, I won't start automatically anymore."}
        if action == "status":
            info = await self.to_thread(autostart.status)
            state = "enabled" if info["enabled"] else "disabled"
            return {**info, "speech": f"Autostart is currently {state}."}
        raise ToolError("Action must be enable, disable or status.")


def get_tools() -> list[BaseTool]:
    return [AutostartTool()]
