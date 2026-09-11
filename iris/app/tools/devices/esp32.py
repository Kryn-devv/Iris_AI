"""ESP32 device control tools.

Lets IRIS drive the user's WiFi hardware — the BTS7960 robot base and the
ESP32-S3 sensor/face board — by calling the small HTTP servers those boards
expose on the LAN (or, for a board that dials in, down its node link).

Works out of the box with the bundled firmware (``firmware/esp32-iris-node-
bts7960`` answers ``/status`` and ``/motor``; ``firmware/esp32-s3-iris-sensors``
answers ``/status``, ``/sensors`` and ``/face``) and with any existing custom
firmware through per-device command maps. See ``docs/ESP32.md``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx

from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError
from iris.app.nodes.link import default_node_hub
from iris.app.tools.devices.transport import device_request, lan_get
from iris.app.tools.devices.registry import (
    DEVICE_KINDS,
    Device,
    DeviceError,
    DeviceRegistry,
    default_device_registry,
    normalize_base_url,
    normalize_name,
)

logger = get_logger("tools.devices.esp32")

_MOTOR_ACTIONS = ("forward", "backward", "left", "right", "stop")

#: Pushed telemetry older than this is not worth trusting for a direct
#: question; ask the node instead.
TELEMETRY_MAX_AGE_S = 12.0


async def _device_get(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET a device endpoint by absolute URL (LAN transport only).

    Kept for the few places that already hold a URL. Prefer
    :func:`device_request`, which also works for a device that dials in.
    """
    return await lan_get(url, params)


def _describe_distances(data: Dict[str, Any]) -> list[str]:
    """One phrase for the nearest thing ahead and behind.

    The board sends ``distances`` per sensor (front_left, front_right,
    rear_left, rear_right; ``null`` for no echo) plus the two minima IRIS has
    always understood, ``distance_cm`` and ``distance_rear_cm``. When one side
    of a pair is much closer than the other, that side is named — "12 cm
    behind on the left" is what stops a robot backing into a chair leg.
    """
    sides = data.get("distances") if isinstance(data.get("distances"), dict) else {}

    def side_phrase(where: str, key_min: str, left: str, right: str) -> Optional[str]:
        nearest = data.get(key_min)
        lv, rv = sides.get(left), sides.get(right)
        if nearest is None:
            candidates = [v for v in (lv, rv) if isinstance(v, (int, float))]
            if not candidates:
                return None
            nearest = min(candidates)
        phrase = f"{nearest} cm {where}"
        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) and abs(lv - rv) > 15:
            phrase += " on the left" if lv < rv else " on the right"
        elif isinstance(lv, (int, float)) and rv is None and right in sides:
            phrase += " on the left"
        elif isinstance(rv, (int, float)) and lv is None and left in sides:
            phrase += " on the right"
        return phrase

    front = side_phrase("ahead", "distance_cm", "front_left", "front_right")
    rear = side_phrase("behind", "distance_rear_cm", "rear_left", "rear_right")
    if front and rear:
        return [f"{front}, {rear}"]
    if front:
        return [f"nearest object {front}"]
    if rear:
        return [rear]
    return []


def _require_device(registry: DeviceRegistry, name: str) -> Device:
    device = registry.get(name)
    if device is None:
        known = ", ".join(d.name for d in registry.list()) or "none yet"
        raise ToolError(
            f"No device named '{name}' is registered (known: {known}). "
            f"Say: add device {name} at 192.168.1.50",
            speech=f"I don't know a device called {name} yet.",
        )
    return device


class RegisterDeviceTool(BaseTool):
    name = "register_device"
    description = (
        "Register an ESP32 board on the local network so IRIS can control it. "
        "Give it a name, its IP address (or .local name) and what it is: motor (the robot base), "
        "sensor or face (the S3 board), or generic."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.LOW_RISK_ACTION
    aliases = ["add device", "add esp32", "pair device", "connect device"]
    network = True
    input_schema = ToolParameterSchema(
        properties={
            "name": {"type": "string", "description": "Friendly name, e.g. 'kitchen light', 'robot'"},
            "address": {"type": "string", "description": "LAN IP or host, e.g. 192.168.1.50 or robot.local"},
            "kind": {"type": "string", "enum": list(DEVICE_KINDS),
                     "description": "motor | sensor | face | generic"},
        },
        required=["name", "address"],
    )
    examples = [
        ToolExample(utterance="add device robot at 192.168.1.60 as motor",
                    arguments={"name": "robot", "address": "192.168.1.60", "kind": "motor"}),
        ToolExample(utterance="add device face at 192.168.1.70 as face",
                    arguments={"name": "face", "address": "192.168.1.70", "kind": "face"}),
    ]

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self, name: str, address: str, kind: str = "generic") -> Dict[str, Any]:
        try:
            device = Device(
                name=normalize_name(name),
                base_url=normalize_base_url(address),
                kind=kind if kind in DEVICE_KINDS else "generic",
            )
        except DeviceError as exc:
            raise ToolError(str(exc)) from exc

        # Best-effort probe: register either way, but tell the user what we saw.
        reachable, status = True, {}
        try:
            status = await device_request(device, "/status")
            if device.kind == "generic" and isinstance(status.get("kind"), str) and status["kind"] in DEVICE_KINDS:
                device.kind = status["kind"]
        except ToolError:
            reachable = False

        self.registry.add(device)
        note = "and it answered my ping" if reachable else "but it did not answer yet — check power and WiFi"
        return {
            "device": device.to_dict(),
            "reachable": reachable,
            "status": status,
            "speech": f"Registered {device.name} at {device.base_url.split('//')[1]}, {note}.",
        }


class ListDevicesTool(BaseTool):
    name = "list_devices"
    description = "List the smart devices / ESP32 nodes registered with IRIS."
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.READ
    aliases = ["my devices", "show devices", "esp32 list"]
    input_schema = ToolParameterSchema()

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self) -> Dict[str, Any]:
        devices = [d.to_dict() for d in self.registry.list()]
        if not devices:
            return {
                "devices": [],
                "speech": "No devices registered yet. Say: add device light at 192.168.1.50",
            }
        names = ", ".join(d["name"] for d in devices)
        return {"devices": devices, "count": len(devices), "speech": f"You have {len(devices)} devices: {names}."}


class RemoveDeviceTool(BaseTool):
    name = "remove_device"
    description = "Remove a registered smart device from IRIS."
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.LOW_RISK_ACTION
    aliases = ["forget device", "delete device", "unpair device"]
    input_schema = ToolParameterSchema(
        properties={"name": {"type": "string", "description": "Device name to remove"}},
        required=["name"],
    )

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self, name: str) -> Dict[str, Any]:
        removed = self.registry.remove(name)
        if not removed:
            raise ToolError(f"No device named '{name}' is registered.")
        return {"removed": name, "speech": f"Removed {name}."}


class DeviceMotorTool(BaseTool):
    name = "device_motor"
    description = (
        "Drive a registered motor device (the robot base): forward, backward, left, right or stop, "
        "with optional speed 0-255 and duration in milliseconds."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ["move robot", "drive", "robot forward", "robot stop"]
    network = True
    mutating = True
    input_schema = ToolParameterSchema(
        properties={
            "action": {"type": "string", "enum": list(_MOTOR_ACTIONS), "description": "Direction or stop"},
            "device": {"type": "string", "description": "Motor device name (defaults to the first motor device)"},
            "speed": {"type": "integer", "minimum": 0, "maximum": 255, "description": "PWM speed 0-255"},
            "duration_ms": {"type": "integer", "minimum": 0, "description": "Auto-stop after this many ms"},
        },
        required=["action"],
    )
    examples = [
        ToolExample(utterance="move the robot forward", arguments={"action": "forward"}),
        ToolExample(utterance="robot stop", arguments={"action": "stop"}),
    ]

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(
        self,
        action: str,
        device: Optional[str] = None,
        speed: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        action = str(action).strip().lower()
        if action not in _MOTOR_ACTIONS:
            raise ToolError(f"Motor action must be one of {', '.join(_MOTOR_ACTIONS)}.")

        # A name that was given but is unknown is a different problem from
        # having no robot at all, and gets the message that names the fix.
        target = _require_device(self.registry, device) if device else self.registry.first_of_kind("motor")
        if target is None:
            raise ToolError(
                "No motor device is registered. Say: add device robot at 192.168.1.60 as motor "
                "after flashing firmware/esp32-iris-node-bts7960.",
                speech="I don't have a robot registered yet.",
            )

        custom = target.command_path(action)
        if custom:
            data = await device_request(target, custom)
        else:
            params: Dict[str, Any] = {"dir": action}
            if speed is not None:
                params["speed"] = max(0, min(255, int(speed)))
            if duration_ms:
                params["ms"] = max(0, int(duration_ms))
            data = await device_request(target, "/motor", params)

        return {
            "device": target.name,
            "action": action,
            "response": data,
            "speech": f"{'Stopping' if action == 'stop' else 'Moving ' + action}.",
        }


class DeviceCommandTool(BaseTool):
    name = "device_command"
    description = (
        "Send a named custom command to a registered device (from its command map), "
        "or GET a relative path on it, e.g. '/motor?dir=stop'. For advanced device control."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ["esp32 command", "send to device"]
    network = True
    mutating = True
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Registered device name"},
            "command": {"type": "string", "description": "Named command from the device's map, or a /path"},
        },
        required=["device", "command"],
    )

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self, device: str, command: str) -> Dict[str, Any]:
        target = _require_device(self.registry, device)
        command = str(command or "").strip()
        if not command:
            raise ToolError("Command is empty.")

        path = target.command_path(command)
        if path is None:
            if not command.startswith("/"):
                known = ", ".join(sorted(target.commands)) or "none"
                raise ToolError(
                    f"'{command}' is not a named command on {target.name} (known: {known}). "
                    "Pass a /path to call the device directly."
                )
            path = command
        if any(seq in path for seq in ("..", "://", "\\\\")):
            raise ToolError("Command paths must be simple relative paths on the device.")

        data = await device_request(target, path)
        return {
            "device": target.name,
            "path": path,
            "response": data,
            "speech": f"Sent {command} to {target.name}.",
        }


class DeviceStatusTool(BaseTool):
    name = "device_status"
    description = "Check whether a registered device is online and read its /status report (or all devices when no name given)."
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.READ
    aliases = ["is the light on", "device online", "ping device"]
    network = True
    input_schema = ToolParameterSchema(
        properties={"device": {"type": "string", "description": "Device name; omit for all devices"}},
    )

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self, device: Optional[str] = None) -> Dict[str, Any]:
        targets = [_require_device(self.registry, device)] if device else self.registry.list()
        if not targets:
            return {"devices": [], "speech": "No devices registered yet."}

        results = []
        online = 0
        for target in targets:
            try:
                status = await device_request(target, "/status")
                online += 1
                results.append({"name": target.name, "online": True, "status": status})
            except ToolError as exc:
                results.append({"name": target.name, "online": False, "error": str(exc)})

        if device:
            one = results[0]
            speech = f"{targets[0].name.capitalize()} is {'online' if one['online'] else 'not responding'}."
        else:
            speech = f"{online} of {len(results)} devices are online."
        return {"devices": results, "speech": speech}


class DeviceSensorsTool(BaseTool):
    name = "device_sensors"
    description = (
        "Read live sensor values from a registered sensor node (ESP32 with motion, gas, "
        "light, flame, temperature, humidity, four ultrasonic distances ahead and behind). Answers "
        "'is there motion', 'gas level', 'how far is the object', 'what's the temperature'."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.READ
    aliases = ["read sensors", "sensor readings", "check motion", "gas level", "temperature", "humidity"]
    network = True
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Sensor node name (defaults to the first sensor device)"},
            "sensor": {"type": "string", "enum": ["all", "motion", "gas", "light", "distance", "flame",
                                  "temperature", "humidity", "climate"],
                        "description": "Which reading to report (default all)"},
        },
    )
    examples = [
        ToolExample(utterance="is there any motion", arguments={"sensor": "motion"}),
        ToolExample(utterance="what's the gas level", arguments={"sensor": "gas"}),
        ToolExample(utterance="how far is the object", arguments={"sensor": "distance"}),
        ToolExample(utterance="is there a fire", arguments={"sensor": "flame"}),
        ToolExample(utterance="what's the temperature", arguments={"sensor": "temperature"}),
        ToolExample(utterance="what's the humidity", arguments={"sensor": "humidity"}),
    ]

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    @staticmethod
    def _summarize(data: Dict[str, Any], sensor: str) -> str:
        parts: list[str] = []
        # Danger first: a flame reading must lead the sentence, not be buried
        # after the light level.
        if sensor in ("all", "flame") and "flame" in data:
            parts.append("FIRE DETECTED" if data.get("flame") else "no flame")
        if sensor in ("all", "gas") and "gas_raw" in data:
            if data.get("gas_alarm"):
                parts.append(f"GAS ALARM — level {data['gas_raw']}")
            else:
                parts.append(f"gas level {data['gas_raw']} (normal)")
        if sensor in ("all", "motion") and "motion_recent" in data:
            parts.append(
                "Motion detected" if data.get("motion") or data.get("motion_recent")
                else "No motion"
            )
        if sensor in ("all", "temperature", "climate") and "temperature_c" in data:
            parts.append(f"{data['temperature_c']} degrees")
        if sensor in ("all", "humidity", "climate") and "humidity_pct" in data:
            parts.append(f"humidity {data['humidity_pct']}%")
        if sensor in ("all", "light") and "light_percent" in data:
            parts.append(f"light {data['light_percent']}%")
        # Four ultrasonics read as one sentence — "40 cm ahead, 12 cm behind"
        # — with the side named only when the two sensors on that side
        # disagree enough to matter. Four bare numbers are noise to a listener.
        if sensor in ("all", "distance"):
            parts.extend(_describe_distances(data))
        if not parts:
            return "The node answered but reported no matching sensors."
        return ", ".join(parts) + "."

    async def _run(self, device: Optional[str] = None, sensor: str = "all") -> Dict[str, Any]:
        target = self.registry.get(device) if device else self._pick_sensor_device()
        if target is None:
            raise ToolError(
                "No sensor node is registered. Flash firmware/esp32-s3-iris-sensors and say: "
                "add device room sensor at 192.168.1.70 as sensor",
                speech="I don't have a sensor node registered yet.",
            )
        sensor = (sensor or "all").strip().lower()

        # A linked node pushes readings continuously, so the newest one is
        # already here. Using it means an instant answer instead of a round
        # trip to a board on the other side of the internet — and it still
        # works during the seconds a node spends reconnecting.
        data, source = self._cached(target), "telemetry"
        if data is None:
            data, source = await device_request(target, "/sensors"), "live"

        summary = self._summarize(data, sensor)
        return {
            "device": target.name,
            "source": source,
            "readings": data,
            "speech": summary,
            "display": f"{target.name}: {summary}",
        }

    def _pick_sensor_device(self) -> Optional[Device]:
        """A face node carries the sensors too, so fall back to it."""
        return (self.registry.first_of_kind("sensor")
                or self.registry.first_of_kind("face"))

    @staticmethod
    def _cached(target: Device) -> Optional[Dict[str, Any]]:
        if not target.linked:
            return None
        link = default_node_hub.get(target.name)
        if link is None or not link.telemetry:
            return None
        age = time.monotonic() - link.telemetry_at
        if age > TELEMETRY_MAX_AGE_S:
            return None        # stale enough that a fresh read is worth the wait
        return dict(link.telemetry)


class MapDeviceCommandTool(BaseTool):
    """Map a named command to a URL path on a device running its OWN custom
    firmware, so an existing sketch works with IRIS without reflashing.

    Example: if a homemade robot already answers a GET to '/go' to move off,
    this tool records "forward -> /go" for that device, and from then on
    'robot forward' calls that exact path instead of the uniform IRIS-node
    ``/motor`` API.
    """

    name = "map_device_command"
    description = (
        "Map a named command (forward, stop, or any custom name) to a URL path on a "
        "registered device that runs its own custom firmware — for existing boards, not "
        "ones flashed with the IRIS node firmware."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.LOW_RISK_ACTION
    aliases = ["map command", "set device command", "map device"]
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Registered device name"},
            "command": {"type": "string", "description": "Command name: forward, stop, or custom"},
            "path": {"type": "string", "description": "URL path on the device, e.g. /go"},
        },
        required=["device", "command", "path"],
    )
    examples = [
        ToolExample(
            utterance="map robot forward command to /go",
            arguments={"device": "robot", "command": "forward", "path": "/go"},
        ),
        ToolExample(
            utterance="set robot stop to /halt",
            arguments={"device": "robot", "command": "stop", "path": "/halt"},
        ),
    ]

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self, device: str, command: str, path: str) -> Dict[str, Any]:
        try:
            target = self.registry.set_command(device, command, path)
        except DeviceError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "device": target.name,
            "commands": target.commands,
            "speech": f"Got it — {target.name} {command} now calls {target.command_path(command)}.",
        }


def get_tools() -> list[BaseTool]:
    return [
        RegisterDeviceTool(),
        MapDeviceCommandTool(),
        ListDevicesTool(),
        RemoveDeviceTool(),
        DeviceMotorTool(),
        DeviceCommandTool(),
        DeviceStatusTool(),
        DeviceSensorsTool(),
    ]
