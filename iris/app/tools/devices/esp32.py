"""ESP32 / smart-device control tools.

Lets IRIS drive the user's WiFi hardware — ESP32 boards running relays
(lights, fans, sockets, home automation) and motor drivers (the robot base) —
by calling the small HTTP servers those boards expose on the LAN.

Works out of the box with the bundled ``firmware/esp32-iris-node`` sketch
(uniform ``/status`` / ``/relay`` / ``/motor`` API) and with any existing
custom firmware through per-device command maps. See ``docs/ESP32.md``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError
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

#: ESP32 web servers answer in well under a second on a healthy LAN.
_TIMEOUT = httpx.Timeout(6.0, connect=3.0)

_MOTOR_ACTIONS = ("forward", "backward", "left", "right", "stop")


async def _device_get(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET a device endpoint, tolerating non-JSON bodies from custom firmware."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, params=params)
    except httpx.ConnectError as exc:
        raise ToolError(
            f"Could not reach the device at {url.split('/', 3)[2]} — is it powered on and on the same WiFi?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ToolError(f"The device at {url.split('/', 3)[2]} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"Device request failed: {exc}") from exc

    if response.status_code >= 400:
        raise ToolError(f"The device answered HTTP {response.status_code}: {response.text[:120]}")
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"response": data}
    except ValueError:
        return {"response": response.text[:400]}


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
        "Register an ESP32 or smart device on the local network so IRIS can control it. "
        "Give it a name, its IP address (or .local name) and what it is: relay (lights, fans, "
        "sockets), motor (robot base) or generic."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.LOW_RISK_ACTION
    aliases = ["add device", "add esp32", "pair device", "connect device"]
    network = True
    input_schema = ToolParameterSchema(
        properties={
            "name": {"type": "string", "description": "Friendly name, e.g. 'kitchen light', 'robot'"},
            "address": {"type": "string", "description": "LAN IP or host, e.g. 192.168.1.50 or robot.local"},
            "kind": {"type": "string", "enum": list(DEVICE_KINDS), "description": "relay | motor | generic"},
            "channel": {"type": "integer", "description": "Relay channel this name controls (default 1)"},
        },
        required=["name", "address"],
    )
    examples = [
        ToolExample(utterance="add device kitchen light at 192.168.1.50",
                    arguments={"name": "kitchen light", "address": "192.168.1.50", "kind": "relay"}),
        ToolExample(utterance="register my robot at robot.local as a motor device",
                    arguments={"name": "robot", "address": "robot.local", "kind": "motor"}),
    ]

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self, name: str, address: str, kind: str = "generic", channel: int = 1) -> Dict[str, Any]:
        try:
            device = Device(
                name=normalize_name(name),
                base_url=normalize_base_url(address),
                kind=kind if kind in DEVICE_KINDS else "generic",
                default_channel=max(1, int(channel)),
            )
        except DeviceError as exc:
            raise ToolError(str(exc)) from exc

        # Best-effort probe: register either way, but tell the user what we saw.
        reachable, status = True, {}
        try:
            status = await _device_get(f"{device.base_url}/status")
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


class DeviceSwitchTool(BaseTool):
    name = "device_switch"
    description = (
        "Turn a registered smart device (light, fan, socket, relay) on or off, or toggle it. "
        "Uses the device's custom command map when defined, otherwise the IRIS node relay API."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ["turn on", "turn off", "switch on", "switch off", "light on", "light off"]
    network = True
    mutating = True
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Registered device name, e.g. 'kitchen light'"},
            "state": {"type": "string", "enum": ["on", "off", "toggle"], "description": "Target state"},
            "channel": {"type": "integer", "description": "Relay channel (defaults to the device's channel)"},
        },
        required=["device", "state"],
    )
    examples = [
        ToolExample(utterance="turn on the kitchen light", arguments={"device": "kitchen light", "state": "on"}),
        ToolExample(utterance="switch off the fan", arguments={"device": "fan", "state": "off"}),
    ]

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(self, device: str, state: str, channel: Optional[int] = None) -> Dict[str, Any]:
        state = str(state).strip().lower()
        if state not in ("on", "off", "toggle"):
            raise ToolError(f"State must be on, off or toggle — got '{state}'.")
        target = _require_device(self.registry, device)

        custom = target.command_path(state)
        if custom:
            data = await _device_get(f"{target.base_url}{custom}")
        else:
            ch = channel or target.default_channel
            data = await _device_get(
                f"{target.base_url}/relay", params={"ch": ch, "state": state}
            )

        spoken_state = data.get("state", state) if isinstance(data, dict) else state
        return {
            "device": target.name,
            "state": spoken_state,
            "response": data,
            "speech": f"{target.name.capitalize()} {'toggled' if state == 'toggle' else 'turned ' + state}.",
        }


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

        target = self.registry.get(device) if device else self.registry.first_of_kind("motor")
        if target is None:
            raise ToolError(
                "No motor device is registered. Say: add device robot at 192.168.1.60 "
                "(kind motor) after flashing the IRIS node firmware.",
                speech="I don't have a robot registered yet.",
            )

        custom = target.command_path(action)
        if custom:
            data = await _device_get(f"{target.base_url}{custom}")
        else:
            params: Dict[str, Any] = {"dir": action}
            if speed is not None:
                params["speed"] = max(0, min(255, int(speed)))
            if duration_ms:
                params["ms"] = max(0, int(duration_ms))
            data = await _device_get(f"{target.base_url}/motor", params=params)

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
        "or GET a relative path on it, e.g. '/servo?angle=90'. For advanced device control."
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

        data = await _device_get(f"{target.base_url}{path}")
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
                status = await _device_get(f"{target.base_url}/status")
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
        "light, ultrasonic distance...). Answers 'is there motion', 'gas level', "
        "'how far is the object'."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.READ
    aliases = ["read sensors", "sensor readings", "check motion", "gas level"]
    network = True
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Sensor node name (defaults to the first sensor device)"},
            "sensor": {"type": "string", "enum": ["all", "motion", "gas", "light", "distance"],
                        "description": "Which reading to report (default all)"},
        },
    )
    examples = [
        ToolExample(utterance="is there any motion", arguments={"sensor": "motion"}),
        ToolExample(utterance="what's the gas level", arguments={"sensor": "gas"}),
        ToolExample(utterance="how far is the object", arguments={"sensor": "distance"}),
    ]

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    @staticmethod
    def _summarize(data: Dict[str, Any], sensor: str) -> str:
        parts: list[str] = []
        if sensor in ("all", "motion") and "motion_recent" in data:
            parts.append(
                "Motion detected" if data.get("motion") or data.get("motion_recent")
                else "No motion"
            )
        if sensor in ("all", "gas") and "gas_raw" in data:
            if data.get("gas_alarm"):
                parts.append(f"GAS ALARM — level {data['gas_raw']}")
            else:
                parts.append(f"gas level {data['gas_raw']} (normal)")
        if sensor in ("all", "light") and "light_percent" in data:
            parts.append(f"light {data['light_percent']}%")
        if sensor in ("all", "distance") and "distance_cm" in data:
            parts.append(f"nearest object {data['distance_cm']} cm away")
        if not parts:
            return "The node answered but reported no matching sensors."
        return ", ".join(parts) + "."

    async def _run(self, device: Optional[str] = None, sensor: str = "all") -> Dict[str, Any]:
        target = self.registry.get(device) if device else self.registry.first_of_kind("sensor")
        if target is None:
            raise ToolError(
                "No sensor node is registered. Flash firmware/esp32-s3-iris-sensors and say: "
                "add device room sensor at 192.168.1.70 as sensor",
                speech="I don't have a sensor node registered yet.",
            )
        sensor = (sensor or "all").strip().lower()
        data = await _device_get(f"{target.base_url}/sensors")
        summary = self._summarize(data, sensor)
        return {
            "device": target.name,
            "readings": data,
            "speech": summary,
            "display": f"{target.name}: {summary}",
        }


class MapDeviceCommandTool(BaseTool):
    """Map a named command to a URL path on a device running its OWN custom
    firmware, so an existing sketch works with IRIS without reflashing.

    Example: if a homemade board already answers a GET to '/led/on' to turn a
    light on, this tool records "on -> /led/on" for that device, and from then
    on 'turn on the kitchen light' calls that exact path instead of the
    uniform IRIS-node relay API.
    """

    name = "map_device_command"
    description = (
        "Map a named command (on, off, toggle, or any custom name) to a URL path on a "
        "registered device that runs its own custom firmware — for existing boards, not "
        "ones flashed with the IRIS node firmware."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.LOW_RISK_ACTION
    aliases = ["map command", "set device command", "map device"]
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Registered device name"},
            "command": {"type": "string", "description": "Command name: on, off, toggle, or custom"},
            "path": {"type": "string", "description": "URL path on the device, e.g. /led/on"},
        },
        required=["device", "command", "path"],
    )
    examples = [
        ToolExample(
            utterance="map kitchen light on command to /led/on",
            arguments={"device": "kitchen light", "command": "on", "path": "/led/on"},
        ),
        ToolExample(
            utterance="set fan off to /relay1/off",
            arguments={"device": "fan", "command": "off", "path": "/relay1/off"},
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
        DeviceSwitchTool(),
        DeviceMotorTool(),
        DeviceCommandTool(),
        DeviceStatusTool(),
        DeviceSensorsTool(),
    ]
