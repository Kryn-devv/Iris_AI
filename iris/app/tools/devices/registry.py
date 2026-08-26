"""Persistent registry of the user's WiFi devices (ESP32 nodes).

Each device is a small HTTP server on the local network — an ESP32 driving
relays (lights, fans, sockets) or motors (the robot base). IRIS stores the
device's name, address and command map in ``devices.json`` under the data
directory, so registrations survive restarts.

Two firmware styles are supported:

* **IRIS node firmware** (``firmware/esp32-iris-node``) — a uniform API
  (``/status``, ``/relay``, ``/motor``); zero per-device configuration.
* **Existing custom firmware** — whatever HTTP endpoints the user already
  built; mapped through the per-device ``commands`` table, e.g.
  ``{"on": "/led/on", "off": "/led/off"}``.

Only private/LAN addresses are accepted: these tools drive hardware relays,
so requests must never be steerable to arbitrary internet hosts.
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from iris.app.core import paths
from iris.app.core.logging import get_logger

logger = get_logger("tools.devices.registry")

REGISTRY_FILENAME = "devices.json"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,31}$")

DEVICE_KINDS = ("relay", "motor", "sensor", "generic")


class DeviceError(ValueError):
    """Raised for invalid device definitions or lookups."""


def normalize_name(name: str) -> str:
    """Canonical device name: lowercase, single spaces."""
    cleaned = " ".join(str(name or "").strip().lower().split())
    if not cleaned or not _NAME_RE.match(cleaned):
        raise DeviceError(
            f"'{name}' is not a valid device name (letters, digits, spaces, - or _; max 32 chars)."
        )
    return cleaned


def normalize_base_url(address: str) -> str:
    """Turn an address ('192.168.1.50', 'robot.local', a URL) into a base URL.

    Rejects anything that is not a private LAN address, loopback, or an
    mDNS ``.local`` name — device control must stay on the local network.
    """
    text = str(address or "").strip().rstrip("/")
    if not text:
        raise DeviceError("Device address is empty.")
    if "://" not in text:
        text = "http://" + text

    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise DeviceError(f"Unsupported scheme '{parts.scheme}' — use http on the LAN.")
    host = parts.hostname or ""
    if not host:
        raise DeviceError(f"Could not parse a host from '{address}'.")

    if not _is_lan_host(host):
        raise DeviceError(
            f"'{host}' is not a local network address. Devices must be on your LAN "
            "(192.168.x.x, 10.x.x.x, 172.16-31.x.x, or a .local mDNS name)."
        )

    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def _is_lan_host(host: str) -> bool:
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".local") or lowered.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


@dataclass
class Device:
    """One registered network device."""

    name: str
    base_url: str
    kind: str = "generic"          # relay | motor | generic
    #: Named custom commands -> relative paths on the device
    #: (for user-built firmware), e.g. {"on": "/led/on", "off": "/led/off"}.
    commands: Dict[str, str] = field(default_factory=dict)
    #: Relay channel the plain on/off commands target (IRIS node firmware).
    default_channel: int = 1
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "kind": self.kind,
            "commands": dict(self.commands),
            "default_channel": self.default_channel,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Device":
        return cls(
            name=normalize_name(data["name"]),
            base_url=normalize_base_url(data["base_url"]),
            kind=data.get("kind", "generic") if data.get("kind") in DEVICE_KINDS else "generic",
            commands={str(k).lower(): str(v) for k, v in (data.get("commands") or {}).items()},
            default_channel=int(data.get("default_channel", 1)),
            notes=str(data.get("notes", "")),
        )

    def command_path(self, command: str) -> Optional[str]:
        """Relative path for a named custom command, if mapped."""
        path = self.commands.get(command.strip().lower())
        if path is None:
            return None
        if not path.startswith("/"):
            path = "/" + path
        return path


class DeviceRegistry:
    """Thread-safe, JSON-persisted collection of devices."""

    def __init__(self, path: Optional[Any] = None):
        self._path = path or (paths.data_dir() / REGISTRY_FILENAME)
        self._lock = threading.Lock()
        self._devices: Dict[str, Device] = {}
        self._load()

    # ----------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for item in raw.get("devices", []):
                try:
                    device = Device.from_dict(item)
                    self._devices[device.name] = device
                except (DeviceError, KeyError, TypeError, ValueError) as exc:
                    logger.warning("Skipping invalid device entry: %s", exc)
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read device registry: %s", exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"devices": [d.to_dict() for d in self._devices.values()]}
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not persist device registry: %s", exc)

    # ------------------------------------------------------------------ CRUD
    def add(self, device: Device) -> Device:
        with self._lock:
            self._devices[device.name] = device
            self._save()
        logger.info("Registered device '%s' (%s) at %s", device.name, device.kind, device.base_url)
        return device

    def remove(self, name: str) -> bool:
        key = normalize_name(name)
        with self._lock:
            existed = self._devices.pop(key, None) is not None
            if existed:
                self._save()
        return existed

    def get(self, name: str) -> Optional[Device]:
        try:
            key = normalize_name(name)
        except DeviceError:
            return None
        device = self._devices.get(key)
        if device:
            return device
        # Forgiving lookup: "the kitchen light" matches device "kitchen light";
        # "light" matches the only device whose name contains it.
        contains = [d for d in self._devices.values() if key in d.name or d.name in key]
        if len(contains) == 1:
            return contains[0]
        return None

    def list(self) -> List[Device]:
        return sorted(self._devices.values(), key=lambda d: d.name)

    def first_of_kind(self, kind: str) -> Optional[Device]:
        for device in self._devices.values():
            if device.kind == kind:
                return device
        return None

    def clear(self) -> None:
        with self._lock:
            self._devices.clear()
            self._save()


default_device_registry = DeviceRegistry()
