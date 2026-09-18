"""Telling the robot to go and work the room out for itself.

One tool with three verbs — start, stop, status — because "explore",
"wander", "ghoomo" and "move on your own" are all the same request, and
"stop" has to reach it whichever way it was started.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from iris.app.core.config import settings
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

ACTIONS = ("start", "stop", "status")


class RobotRoamTool(BaseTool):
    name = "robot_roam"
    description = (
        "Let the robot drive itself around using its ultrasonic sensors — exploring, "
        "avoiding obstacles and getting itself out of corners with nobody steering. "
        "Answers 'explore', 'wander around', 'move on your own', 'roam', 'drive "
        "yourself', and 'stop exploring'. Use action=status to report what it is doing."
    )
    category = ToolCategory.AUTOMATION
    #: The same level as ``device_motor`` and ``robot_navigate``, which is
    #: where the project already puts "this makes the robot move". HIGH_RISK
    #: was tempting and wrong: it is off by default, so with it set the robot
    #: could be driving and **"stop exploring" would be refused too**. Whatever
    #: gate starting passes through, stopping has to be on the safe side of.
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ["explore", "wander", "roam", "drive yourself", "move on your own"]
    input_schema = ToolParameterSchema(
        properties={
            "action": {"type": "string", "enum": list(ACTIONS),
                       "description": "start, stop, or status (default start)."},
            "minutes": {"type": "number", "minimum": 0.1, "maximum": 120,
                        "description": "Roam for this long before parking itself."},
        },
    )
    examples = [
        ToolExample(utterance="go explore", arguments={"action": "start"}),
        ToolExample(utterance="wander around for 2 minutes",
                    arguments={"action": "start", "minutes": 2}),
        ToolExample(utterance="stop exploring", arguments={"action": "stop"}),
    ]

    def __init__(self, service: Optional[Any] = None):
        super().__init__()
        self._service = service

    @property
    def service(self):
        if self._service is not None:
            return self._service
        from iris.app.services.roam import default_roam_service

        return default_roam_service

    async def _run(
        self,
        action: str = "start",
        minutes: Optional[float] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        action = (action or "start").strip().lower()
        if action not in ACTIONS:
            raise ToolError(f"I can start, stop or report on roaming — not '{action}'.")

        service = self.service
        state = service.status()

        if action == "status":
            return {"speech": _describe(state), "display": _describe(state), **state}

        if action == "stop":
            await service.set_enabled(False, reason="you asked")
            return {"speech": "Stopped. Wheels are parked.",
                    "display": "Roaming stopped.", **service.status()}

        # ------------------------------------------------------------ start
        if state.get("robot") is None:
            raise ToolError(
                "No motor device is registered. Say: add device robot at 192.168.1.60 as motor.",
                speech="I don't have a robot registered yet.",
            )
        if state.get("senses") is None:
            raise ToolError(
                "I can drive but I can't see — no sense board is registered, and I "
                "won't move without distance sensors.",
                speech="I won't drive myself without the distance sensors.",
            )
        if minutes is not None:
            settings.ROBOT_ROAM_MAX_MINUTES = max(0.1, min(120.0, float(minutes)))

        await service.set_enabled(True)
        window = float(settings.ROBOT_ROAM_MAX_MINUTES)
        spoken = (f"Off I go. I'll explore for about {_minutes(window)} "
                  "and stop if I lose the sensors.")
        return {
            "speech": spoken,
            "display": f"Roaming. Stopping after {_minutes(window)}, or when you say stop.",
            **service.status(),
        }


def _minutes(value: float) -> str:
    if value <= 0:
        return "as long as it takes"
    if value < 1:
        return f"{int(round(value * 60))} seconds"
    whole = int(round(value))
    return f"{whole} minute{'s' if whole != 1 else ''}"


def _describe(state: Dict[str, Any]) -> str:
    if not state.get("ready"):
        if not state.get("robot"):
            return "I can't roam — no robot is registered."
        return "I can't roam — the distance sensors aren't registered, and I won't drive blind."
    if not state.get("enabled"):
        why = state.get("stopped_reason")
        return f"Not roaming right now{f' — stopped because of {why}' if why else ''}."
    last = state.get("last") or {}
    behaviour = last.get("behaviour", "thinking")
    reason = last.get("reason", "")
    metres = state.get("metres", 0)
    return (f"Roaming — {behaviour}{f', {reason}' if reason else ''}. "
            f"About {metres} metres so far.")


def get_tools() -> list[BaseTool]:
    return [RobotRoamTool()]
