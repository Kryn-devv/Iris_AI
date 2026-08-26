"""Live outbound links from ESP32 nodes to IRIS.

**Why this exists.** Everywhere else IRIS reaches a device by calling its IP on
the LAN. That works when IRIS runs on the same network. It cannot work when
IRIS runs on a cloud VPS: the ESP32 sits behind a home router doing NAT, and
there is no route in. Port-forwarding would create one, but it would also put
an unauthenticated microcontroller web server on the public internet.

So the direction is inverted. The **node dials out** to IRIS over a WebSocket
and holds that connection open; commands travel back down the same socket. This
is how commercial IoT devices work, and it needs no broker, no port-forwarding
and no static home IP.

The same socket carries three kinds of traffic:

* **commands** — IRIS asks, the node answers, correlated by request id
* **telemetry** — the node pushes sensor readings on its own schedule, so
  "is there motion?" is answered from the newest reading instead of waiting
  for a round trip to the other side of the world
* **alerts** — the node reports flame or gas the moment it sees it, rather
  than when someone next asks

A command channel reachable from the internet is only ever as safe as its
authentication, so a token is **required**: with none configured the endpoint
refuses every connection rather than accepting anonymous ones.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from iris.app.core.logging import get_logger

logger = get_logger("nodes.link")

#: A node that has said nothing at all for this long is treated as gone, even
#: if the socket has not reported an error — a half-open TCP connection through
#: a NAT looks alive to us and dead to the device.
STALE_AFTER_S = 90.0

#: How long to wait for a node to answer one command. A node on a home
#: connection is ~50-200 ms away; anything slower is not worth blocking a
#: spoken reply for.
REQUEST_TIMEOUT_S = 6.0

#: Refuse absurd frames rather than buffering them.
MAX_FRAME_BYTES = 64 * 1024

#: A node with a runaway loop must not be able to flood the event bus.
MIN_TELEMETRY_INTERVAL_S = 0.4

#: Sensor keys that mean "something is wrong right now".
ALERT_KEYS = ("flame", "gas_alarm", "smoke")


class NodeLinkError(RuntimeError):
    """Raised when a command cannot be delivered to a node."""


@dataclass
class NodeLink:
    """One live connection from one node.

    ``send`` is injected rather than holding a WebSocket directly, so the hub
    and its tests never depend on the web framework.
    """

    name: str
    kind: str
    send: Callable[[str], Awaitable[None]]
    remote: str = ""
    firmware: str = ""
    sensors: List[str] = field(default_factory=list)
    connected_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    telemetry: Dict[str, Any] = field(default_factory=dict)
    telemetry_at: float = 0.0
    commands_sent: int = 0
    telemetry_count: int = 0

    _next_id: int = 1
    _pending: Dict[int, asyncio.Future] = field(default_factory=dict)
    _closed: bool = False

    # ------------------------------------------------------------------ state
    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def stale(self) -> bool:
        return (time.monotonic() - self.last_seen) > STALE_AFTER_S

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def info(self) -> Dict[str, Any]:
        age = time.monotonic() - self.telemetry_at if self.telemetry_at else None
        return {
            "name": self.name,
            "kind": self.kind,
            "remote": self.remote,
            "firmware": self.firmware,
            "sensors": list(self.sensors),
            "uptime_s": round(time.monotonic() - self.connected_at, 1),
            "last_seen_s_ago": round(time.monotonic() - self.last_seen, 1),
            "telemetry_age_s": round(age, 1) if age is not None else None,
            "telemetry": dict(self.telemetry),
            "commands_sent": self.commands_sent,
            "telemetry_count": self.telemetry_count,
        }

    # --------------------------------------------------------------- requests
    async def request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> Dict[str, Any]:
        """Send one command and wait for the node's reply."""
        if self._closed:
            raise NodeLinkError(f"'{self.name}' is not connected.")

        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        frame = json.dumps({
            "type": "cmd",
            "id": request_id,
            "path": path,
            "params": {k: v for k, v in (params or {}).items() if v is not None},
        })
        try:
            await self.send(frame)
            self.commands_sent += 1
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise NodeLinkError(
                f"'{self.name}' did not answer in {timeout:.0f}s — it may have lost WiFi."
            ) from exc
        except NodeLinkError:
            raise
        except Exception as exc:  # noqa: BLE001 - socket died mid-send
            raise NodeLinkError(f"Could not reach '{self.name}': {exc}") from exc
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: int, ok: bool, data: Any) -> None:
        """Hand a reply frame to whoever is waiting for it."""
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return          # timed out already, or a duplicate reply
        if ok:
            future.set_result(data if isinstance(data, dict) else {"response": data})
        else:
            future.set_exception(NodeLinkError(str(data or "the node reported an error")))

    def close(self, reason: str = "closed") -> None:
        """Fail every in-flight request rather than leaving callers hanging."""
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(NodeLinkError(f"'{self.name}' disconnected ({reason})."))
        self._pending.clear()


class NodeLinkHub:
    """Every live node link, keyed by device name."""

    def __init__(self) -> None:
        self._links: Dict[str, NodeLink] = {}
        self._on_telemetry: Optional[Callable[[NodeLink, Dict[str, Any]], None]] = None
        self._on_alert: Optional[Callable[[NodeLink, Dict[str, Any]], None]] = None

    # -------------------------------------------------------------- observers
    def set_observers(
        self,
        on_telemetry: Optional[Callable[[NodeLink, Dict[str, Any]], None]] = None,
        on_alert: Optional[Callable[[NodeLink, Dict[str, Any]], None]] = None,
    ) -> None:
        """Register the callbacks that publish to the event bus.

        Injected rather than imported so this module stays independent of the
        bus, the voice service and everything else that reacts to a sensor.
        """
        self._on_telemetry = on_telemetry
        self._on_alert = on_alert

    # ------------------------------------------------------------- membership
    def register(self, link: NodeLink) -> Optional[NodeLink]:
        """Add a link, returning any previous one for the same name.

        A node that reconnects after a dropped socket arrives while the old
        link still looks alive — a half-open connection through NAT reports no
        error to us. The newest connection wins; the caller closes the old one.
        """
        previous = self._links.get(link.name)
        self._links[link.name] = link
        logger.info(
            "Node '%s' (%s) linked from %s%s",
            link.name, link.kind, link.remote or "?",
            " (replacing an earlier connection)" if previous else "",
        )
        return previous

    def unregister(self, link: NodeLink, reason: str = "closed") -> None:
        if self._links.get(link.name) is link:
            del self._links[link.name]
            logger.info("Node '%s' unlinked (%s)", link.name, reason)
        link.close(reason)

    def get(self, name: str) -> Optional[NodeLink]:
        link = self._links.get(name)
        if link is None or link.closed:
            return None
        return link

    def first_of_kind(self, kind: str) -> Optional[NodeLink]:
        for link in self._links.values():
            if link.kind == kind and not link.closed:
                return link
        return None

    def list(self) -> List[NodeLink]:
        return sorted(self._links.values(), key=lambda link: link.name)

    def info(self) -> List[Dict[str, Any]]:
        return [link.info() for link in self.list()]

    @property
    def count(self) -> int:
        return len(self._links)

    def drop_stale(self) -> List[NodeLink]:
        """Close links that have gone quiet. Returns the ones dropped."""
        dropped = [link for link in list(self._links.values()) if link.stale]
        for link in dropped:
            self.unregister(link, "stale")
        return dropped

    # ---------------------------------------------------------------- traffic
    async def request(
        self,
        name: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> Dict[str, Any]:
        link = self.get(name)
        if link is None:
            raise NodeLinkError(
                f"'{name}' is not connected right now. It dials in to IRIS by itself, "
                "so check that the board is powered on and has WiFi."
            )
        return await link.request(path, params, timeout=timeout)

    def handle_message(self, link: NodeLink, raw: str) -> Optional[Dict[str, Any]]:
        """Process one frame from a node. Returns a frame to send back, if any.

        Everything in ``raw`` comes from a device on someone's home network, so
        it is parsed defensively and never trusted to be well formed.
        """
        link.touch()
        if len(raw) > MAX_FRAME_BYTES:
            logger.warning("Node '%s' sent an oversized frame (%d bytes)", link.name, len(raw))
            return {"type": "error", "error": "frame too large"}
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            return {"type": "error", "error": "not JSON"}
        if not isinstance(message, dict):
            return {"type": "error", "error": "expected a JSON object"}

        kind = str(message.get("type") or "").lower()

        if kind == "reply":
            try:
                request_id = int(message.get("id"))
            except (TypeError, ValueError):
                return {"type": "error", "error": "reply without a numeric id"}
            link.resolve(request_id, bool(message.get("ok", True)),
                         message.get("data", message.get("error")))
            return None

        if kind == "telemetry":
            readings = message.get("sensors")
            if isinstance(readings, dict):
                self._accept_telemetry(link, readings)
            return None

        if kind == "alert":
            self._accept_alert(link, message)
            return None

        if kind == "ping":
            return {"type": "pong"}
        if kind == "pong":
            return None
        if kind == "hello":
            self._accept_hello(link, message)
            return {"type": "welcome", "name": link.name}

        return {"type": "error", "error": f"unknown frame type '{kind}'"}

    def _accept_hello(self, link: NodeLink, message: Dict[str, Any]) -> None:
        firmware = message.get("firmware")
        if isinstance(firmware, str):
            link.firmware = firmware[:64]
        sensors = message.get("sensors")
        if isinstance(sensors, list):
            link.sensors = [str(s)[:24] for s in sensors[:16]]

    def _accept_telemetry(self, link: NodeLink, readings: Dict[str, Any]) -> None:
        now = time.monotonic()
        # A node stuck in a tight loop would otherwise flood the bus and the UI.
        if link.telemetry_at and (now - link.telemetry_at) < MIN_TELEMETRY_INTERVAL_S:
            link.telemetry.update(_clean_readings(readings))
            return
        previous = dict(link.telemetry)
        link.telemetry.update(_clean_readings(readings))
        link.telemetry_at = now
        link.telemetry_count += 1
        if self._on_telemetry is not None:
            try:
                self._on_telemetry(link, dict(link.telemetry))
            except Exception as exc:  # noqa: BLE001 - an observer must not kill the link
                logger.debug("Telemetry observer failed: %s", exc)
        # A reading that has just crossed into danger is an alert in its own
        # right, even when the node did not send one — the node may be older
        # firmware, and a flame is not something to find out about on request.
        for key in ALERT_KEYS:
            if _is_true(link.telemetry.get(key)) and not _is_true(previous.get(key)):
                self._accept_alert(link, {"kind": key, "source": "telemetry"})

    def _accept_alert(self, link: NodeLink, message: Dict[str, Any]) -> None:
        alert = {
            "node": link.name,
            "kind": str(message.get("kind") or "unknown")[:32],
            "message": str(message.get("message") or "")[:200],
            "value": message.get("value"),
            "source": str(message.get("source") or "node")[:16],
        }
        logger.warning("Node '%s' alert: %s", link.name, alert["kind"])
        if self._on_alert is not None:
            try:
                self._on_alert(link, alert)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Alert observer failed: %s", exc)


def _clean_readings(readings: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only small, plainly-typed values from an untrusted node."""
    cleaned: Dict[str, Any] = {}
    for key, value in list(readings.items())[:32]:
        name = str(key)[:32]
        if isinstance(value, bool) or isinstance(value, (int, float)):
            cleaned[name] = value
        elif isinstance(value, str):
            cleaned[name] = value[:64]
    return cleaned


def _is_true(value: Any) -> bool:
    """Truthiness as a sensor means it, not as Python means it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "alarm")
    return False


default_node_hub = NodeLinkHub()
