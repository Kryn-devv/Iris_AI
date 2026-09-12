"""Going somewhere, not just moving.

``device_motor`` is a reflex: one direction, one speed, an auto-stop. This is
the layer above it, where the robot is given a *goal* and works out the rest:

* turn by an angle — "take a U-turn" is 180°, a corner is 90°
* drive a distance — "go two metres" becomes a timed leg from the calibrated speed
* drive until something is there — "go back to the board": forward until the
  front ultrasonics say the target is 35 cm away, then stop
* a plan of several legs — "turn around and come back"

While a leg runs it keeps looking. Every 150 ms it asks the sense board for
the distances ahead (or behind, when reversing), and if anything is closer
than the safety margin it stops the leg early and says so. That is the robot
deciding for itself, not the person at the keyboard. "Stop" from the user
aborts the whole plan at once.

No encoders and no IMU on this robot, so distance and angle come from time and
two calibration numbers (cm/s and deg/s), set in .env or by telling IRIS "one
metre takes three seconds" / "a U-turn takes two seconds". The firmware's own
auto-stop is always sent as well, so a dropped WiFi packet cannot leave the
wheels turning.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError
from iris.app.tools.devices.registry import Device, DeviceRegistry, default_device_registry
from iris.app.tools.devices.transport import device_request

logger = get_logger("tools.devices.navigate")

#: How often the distances are checked while a leg runs.
SENSE_INTERVAL_S = 0.15
#: Consecutive sensor failures before a leg is stopped: no eyes, no driving.
SENSE_FAILURES_TO_STOP = 3
CALIBRATION_FILENAME = "robot_calibration.json"

STEP_KINDS = ("turn", "move", "until_obstacle", "wait")
PRESETS = ("u_turn", "come_back", "to_obstacle")


class _Abort:
    """"Stop" from the user, seen by a plan in flight."""

    flag = False


def request_stop() -> None:
    """Called by the motor tool's stop: whatever plan is running gives up."""
    _Abort.flag = True


def calibration_path() -> Path:
    return paths.data_dir() / CALIBRATION_FILENAME


def load_calibration() -> Dict[str, float]:
    """cm/s and deg/s: the saved values, else the settings defaults."""
    cal = {
        "cm_per_s": float(settings.ROBOT_CM_PER_S),
        "deg_per_s": float(settings.ROBOT_DEG_PER_S),
    }
    try:
        saved = json.loads(calibration_path().read_text())
        for key in cal:
            value = float(saved.get(key, cal[key]))
            if value > 0:
                cal[key] = value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return cal


def save_calibration(**values: float) -> Dict[str, float]:
    cal = load_calibration()
    for key, value in values.items():
        if key in cal and value and float(value) > 0:
            cal[key] = float(value)
    try:
        calibration_path().parent.mkdir(parents=True, exist_ok=True)
        calibration_path().write_text(json.dumps(cal, indent=2))
    except OSError as exc:
        logger.warning("Could not save robot calibration: %s", exc)
    return cal


def fmt_cm(cm: float) -> str:
    cm = max(0.0, float(cm))
    if cm < 100:
        return f"{int(round(cm))} centimetres"
    metres = cm / 100.0
    text = f"{metres:.1f}".rstrip("0").rstrip(".")
    return f"{text} metre{'s' if text != '1' else ''}"


def nearest_distance(readings: Dict[str, Any], direction: str) -> Optional[float]:
    """The closest thing the S3 board sees in the direction of travel, in cm.

    Prefers the per-sensor ``distances`` map (front_left, front_right,
    rear_left, rear_right); falls back to the older single ``distance_cm`` /
    ``distance_rear_cm`` fields. ``None`` when nothing answered.
    """
    side = "rear" if direction == "backward" else "front"
    values: List[float] = []
    distances = readings.get("distances")
    if isinstance(distances, dict):
        for name, value in distances.items():
            if str(name).startswith(side):
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    continue
                if v >= 0:
                    values.append(v)
    if not values:
        key = "distance_rear_cm" if side == "rear" else "distance_cm"
        try:
            v = float(readings.get(key))
            if v >= 0:
                values.append(v)
        except (TypeError, ValueError):
            pass
    return min(values) if values else None


class RobotNavigateTool(BaseTool):
    name = "robot_navigate"
    description = (
        "Give the robot a goal, not just a direction: turn by an angle (a U-turn is 180), "
        "drive a distance in centimetres, drive until the ultrasonics see something within "
        "stop_cm (e.g. 'go back to the board'), or a plan of several such steps. It watches "
        "the distance sensors while moving and stops on its own if something gets close. "
        "Also stores calibration: how many cm per second it drives, degrees per second it turns."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ["u-turn", "turn around", "go back to the board", "come back", "go 2 metres"]
    network = True
    mutating = True
    input_schema = ToolParameterSchema(
        properties={
            "preset": {
                "type": "string", "enum": list(PRESETS),
                "description": "u_turn: turn 180. come_back: turn 180 then drive until something is "
                               "ahead. to_obstacle: drive forward until something is ahead.",
            },
            "steps": {
                "type": "array",
                "description": "An explicit plan, run in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(STEP_KINDS)},
                        "degrees": {"type": "number", "description": "turn: positive = right, negative = left"},
                        "distance_cm": {"type": "number", "description": "move: how far"},
                        "direction": {"type": "string", "enum": ["forward", "backward"]},
                        "stop_cm": {"type": "integer", "description": "until_obstacle/move: stop this close"},
                        "seconds": {"type": "number", "description": "wait: how long"},
                        "target": {"type": "string", "description": "what it is driving toward, for the reply"},
                    },
                },
            },
            "turn_degrees": {"type": "number", "description": "Shortcut: a single turn (positive = right)."},
            "distance_cm": {"type": "number", "description": "Shortcut: a single straight leg."},
            "direction": {"type": "string", "enum": ["forward", "backward"]},
            "until_obstacle": {"type": "boolean", "description": "Shortcut: drive until something is within stop_cm."},
            "stop_cm": {"type": "integer", "description": "Safety margin in cm (default from ROBOT_OBSTACLE_STOP_CM)."},
            "target": {"type": "string", "description": "What it is heading for, e.g. 'the board'."},
            "calibrate_cm_per_s": {"type": "number", "description": "Store how fast it really drives."},
            "calibrate_deg_per_s": {"type": "number", "description": "Store how fast it really turns."},
            "device": {"type": "string", "description": "Motor device name (defaults to the first)."},
        },
    )
    examples = [
        ToolExample(utterance="take a U-turn", arguments={"preset": "u_turn"}),
        ToolExample(utterance="turn around and go back to the board",
                    arguments={"preset": "come_back", "target": "the board"}),
        ToolExample(utterance="go forward 2 metres", arguments={"distance_cm": 200, "direction": "forward"}),
        ToolExample(utterance="turn right 90 degrees", arguments={"turn_degrees": 90}),
        ToolExample(utterance="drive until you reach the wall",
                    arguments={"until_obstacle": True, "target": "the wall"}),
    ]

    #: Swapped by tests so a plan runs in no real time.
    _sleep = staticmethod(asyncio.sleep)
    _clock = staticmethod(time.monotonic)

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    # ------------------------------------------------------------------ run
    async def _run(
        self,
        preset: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        turn_degrees: Optional[float] = None,
        distance_cm: Optional[float] = None,
        direction: str = "forward",
        until_obstacle: bool = False,
        stop_cm: Optional[int] = None,
        target: Optional[str] = None,
        calibrate_cm_per_s: Optional[float] = None,
        calibrate_deg_per_s: Optional[float] = None,
        device: Optional[str] = None,
    ) -> Dict[str, Any]:
        if calibrate_cm_per_s or calibrate_deg_per_s:
            cal = save_calibration(cm_per_s=calibrate_cm_per_s or 0, deg_per_s=calibrate_deg_per_s or 0)
            parts = []
            if calibrate_cm_per_s:
                parts.append(f"{cal['cm_per_s']:g} centimetres a second when driving")
            if calibrate_deg_per_s:
                parts.append(f"{cal['deg_per_s']:g} degrees a second when turning")
            speech = "Noted — " + " and ".join(parts) + "."
            return {"speech": speech, "display": speech, "calibration": cal}

        plan = self._plan(preset, steps, turn_degrees, distance_cm, direction, until_obstacle, stop_cm, target)
        if not plan:
            raise ToolError(
                "Tell me how far to go, how much to turn, or what to drive up to.",
                speech="Tell me how far, how much to turn, or what to reach.",
            )

        robot = self._robot(device)
        senses = self._senses()
        cal = load_calibration()
        _Abort.flag = False

        legs: List[Dict[str, Any]] = []
        for step in plan:
            if _Abort.flag:
                legs.append({"kind": step["kind"], "aborted": True})
                break
            leg = await self._leg(robot, senses, step, cal)
            legs.append(leg)
            if leg.get("aborted") or leg.get("blocked_cm") is not None or leg.get("gave_up"):
                break        # something is in the way, or the user said stop

        speech = self._describe(legs, senses is not None)
        return {
            "speech": speech,
            "display": speech,
            "device": robot.name,
            "senses": senses.name if senses else None,
            "legs": legs,
            # A move cut short by an obstacle is a decision, not a completion;
            # an until_obstacle leg that found its target is the goal itself.
            "completed": len(legs) == len(plan) and all(
                not (l.get("aborted") or l.get("gave_up") or l.get("lost_senses")
                     or (l.get("kind") == "move" and l.get("blocked_cm") is not None))
                for l in legs
            ),
            "calibration": cal,
        }

    # ----------------------------------------------------------------- plan
    def _plan(self, preset, steps, turn_degrees, distance_cm, direction, until_obstacle, stop_cm, target):
        margin = int(stop_cm or settings.ROBOT_OBSTACLE_STOP_CM)
        direction = "backward" if str(direction or "").lower().startswith("back") else "forward"
        plan: List[Dict[str, Any]] = []
        if steps:
            for raw in steps:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("kind") or "").lower()
                if kind not in STEP_KINDS:
                    continue
                step = {"kind": kind, "target": raw.get("target") or target}
                if kind == "turn":
                    step["degrees"] = float(raw.get("degrees") or 0)
                elif kind == "move":
                    step["distance_cm"] = float(raw.get("distance_cm") or 0)
                    step["direction"] = "backward" if str(raw.get("direction") or direction).startswith("back") else "forward"
                    step["stop_cm"] = int(raw.get("stop_cm") or margin)
                elif kind == "until_obstacle":
                    step["direction"] = "backward" if str(raw.get("direction") or direction).startswith("back") else "forward"
                    step["stop_cm"] = int(raw.get("stop_cm") or margin)
                else:
                    step["seconds"] = float(raw.get("seconds") or 0)
                plan.append(step)
            return [s for s in plan if s["kind"] == "until_obstacle" or s.get("degrees") or s.get("distance_cm") or s.get("seconds")]

        if preset == "u_turn":
            plan.append({"kind": "turn", "degrees": 180.0})
        elif preset == "come_back":
            plan.append({"kind": "turn", "degrees": 180.0})
            plan.append({"kind": "until_obstacle", "direction": "forward", "stop_cm": margin, "target": target})
        elif preset == "to_obstacle":
            plan.append({"kind": "until_obstacle", "direction": direction, "stop_cm": margin, "target": target})
        if turn_degrees:
            plan.append({"kind": "turn", "degrees": float(turn_degrees)})
        if distance_cm:
            plan.append({"kind": "move", "distance_cm": float(distance_cm), "direction": direction,
                         "stop_cm": margin, "target": target})
        if until_obstacle and preset not in ("come_back", "to_obstacle"):
            plan.append({"kind": "until_obstacle", "direction": direction, "stop_cm": margin, "target": target})
        return plan

    # -------------------------------------------------------------- devices
    def _robot(self, device: Optional[str]) -> Device:
        target = self.registry.get(device) if device else self.registry.first_of_kind("motor")
        if target is None:
            raise ToolError(
                "No motor device is registered. Say: add device robot at 192.168.1.60 as motor.",
                speech="I don't have a robot registered yet.",
            )
        return target

    def _senses(self) -> Optional[Device]:
        return self.registry.first_of_kind("sensor") or self.registry.first_of_kind("face")

    async def _drive(self, robot: Device, action: str, speed: Optional[int], ms: Optional[int]) -> None:
        custom = robot.command_path(action)
        if custom:
            await device_request(robot, custom)
            return
        params: Dict[str, Any] = {"dir": action}
        if speed is not None:
            params["speed"] = max(0, min(255, int(speed)))
        if ms:
            params["ms"] = max(0, int(ms))
        await device_request(robot, "/motor", params)

    async def _stop(self, robot: Device) -> None:
        try:
            await self._drive(robot, "stop", None, None)
        except ToolError as exc:
            # The firmware's own auto-stop (ms) is the backstop for exactly this.
            logger.warning("Stop did not reach %s: %s", robot.name, exc)

    async def _distance(self, senses: Device, direction: str) -> Optional[float]:
        data = await device_request(senses, "/sensors")
        return nearest_distance(data if isinstance(data, dict) else {}, direction)

    # ------------------------------------------------------------------ legs
    async def _leg(self, robot: Device, senses: Optional[Device], step: Dict[str, Any], cal: Dict[str, float]) -> Dict[str, Any]:
        kind = step["kind"]
        max_ms = int(float(settings.ROBOT_MAX_LEG_S) * 1000)

        if kind == "wait":
            seconds = min(float(step.get("seconds") or 0), float(settings.ROBOT_MAX_LEG_S))
            await self._pause(seconds * 1000)
            return {"kind": "wait", "seconds": seconds, "aborted": _Abort.flag}

        if kind == "turn":
            degrees = float(step["degrees"])
            action = "right" if degrees > 0 else "left"
            ms = min(max_ms, int(abs(degrees) / cal["deg_per_s"] * 1000))
            await self._drive(robot, action, int(settings.ROBOT_TURN_SPEED), ms)
            aborted = await self._pause(ms)
            if aborted:
                await self._stop(robot)
            return {"kind": "turn", "degrees": degrees, "ms": ms, "aborted": aborted}

        direction = step.get("direction", "forward")
        stop_cm = int(step.get("stop_cm") or settings.ROBOT_OBSTACLE_STOP_CM)
        leg: Dict[str, Any] = {"kind": kind, "direction": direction, "stop_cm": stop_cm,
                               "target": step.get("target"), "blocked_cm": None, "aborted": False}

        if kind == "move":
            planned = float(step["distance_cm"])
            ms = min(max_ms, int(planned / cal["cm_per_s"] * 1000))
            leg["planned_cm"] = planned
            leg["blind"] = senses is None
        else:
            if senses is None:
                raise ToolError(
                    "I can't drive up to something without the sense board: register the S3 "
                    "(add device face at <ip> as face) so I can see the distance ahead.",
                    speech="I need my distance sensors for that, and the sense board isn't registered.",
                )
            ms = max_ms
            leg["planned_cm"] = None

        await self._drive(robot, direction, int(settings.ROBOT_CRUISE_SPEED), ms)
        started = self._clock()
        failures = 0
        stopped_by_us = False
        while True:
            await self._sleep(SENSE_INTERVAL_S)
            elapsed_ms = (self._clock() - started) * 1000
            if _Abort.flag:
                leg["aborted"] = True
                stopped_by_us = True
                break
            if elapsed_ms >= ms:
                if kind == "until_obstacle":
                    leg["gave_up"] = True
                    stopped_by_us = True
                break
            if senses is None:
                continue
            try:
                distance = await self._distance(senses, direction)
                failures = 0
            except ToolError as exc:
                failures += 1
                logger.debug("Distance read failed while driving: %s", exc)
                if failures >= SENSE_FAILURES_TO_STOP:
                    leg["lost_senses"] = True
                    stopped_by_us = True
                    break
                continue
            if distance is not None:
                leg["last_cm"] = distance
                if distance <= stop_cm:
                    leg["blocked_cm"] = distance
                    stopped_by_us = True
                    break
        elapsed_s = self._clock() - started
        leg["travelled_cm"] = round(min(cal["cm_per_s"] * elapsed_s, leg["planned_cm"] or 1e9), 1)
        leg["seconds"] = round(elapsed_s, 2)
        if stopped_by_us:
            await self._stop(robot)
        return leg

    async def _pause(self, ms: float) -> bool:
        """Wait out a leg in small slices so a stop is honoured quickly.
        Returns True if the user aborted."""
        started = self._clock()
        while (self._clock() - started) * 1000 < ms:
            await self._sleep(min(SENSE_INTERVAL_S, max(0.01, ms / 1000)))
            if _Abort.flag:
                return True
        return False

    # ---------------------------------------------------------------- words
    @staticmethod
    def _describe(legs: List[Dict[str, Any]], has_senses: bool) -> str:
        parts: List[str] = []
        for leg in legs:
            kind = leg.get("kind")
            if leg.get("aborted") and kind not in ("move", "until_obstacle"):
                parts.append("stopped when you told me to")
                break
            if kind == "turn":
                deg = abs(leg["degrees"])
                if 170 <= deg <= 190:
                    parts.append("turned around")
                else:
                    parts.append(f"turned {'right' if leg['degrees'] > 0 else 'left'} {int(round(deg))} degrees")
            elif kind == "wait":
                parts.append(f"waited {leg['seconds']:g} seconds")
            elif kind == "move":
                how = fmt_cm(leg.get("travelled_cm", 0))
                if leg.get("aborted"):
                    parts.append(f"drove {how} and stopped when you told me to")
                    break
                if leg.get("blocked_cm") is not None:
                    parts.append(f"drove {how} and stopped — something was {fmt_cm(leg['blocked_cm'])} "
                                 f"{'behind' if leg.get('direction') == 'backward' else 'ahead'}")
                    break
                if leg.get("lost_senses"):
                    parts.append(f"drove {how} and stopped because I lost my distance sensors")
                    break
                text = f"drove {fmt_cm(leg.get('planned_cm') or 0)}"
                if leg.get("blind"):
                    text += " without distance sensors, so I couldn't watch for obstacles"
                parts.append(text)
            elif kind == "until_obstacle":
                what = leg.get("target") or "something"
                where = "behind" if leg.get("direction") == "backward" else "ahead"
                if leg.get("aborted"):
                    parts.append("stopped when you told me to")
                    break
                if leg.get("blocked_cm") is not None:
                    parts.append(f"drove until {what} was {fmt_cm(leg['blocked_cm'])} {where}")
                elif leg.get("lost_senses"):
                    parts.append("stopped because I lost my distance sensors")
                    break
                else:
                    parts.append(f"drove {fmt_cm(leg.get('travelled_cm', 0))} without finding {what}, so I stopped")
                    break
        if not parts:
            return "Nothing to do."
        if len(parts) == 1:
            return "Done: " + parts[0] + "."
        return "Done: " + ", then ".join(parts) + "."


def get_tools() -> list[BaseTool]:
    return [RobotNavigateTool()]
