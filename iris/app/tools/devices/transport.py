"""One way to talk to a device, whichever direction the connection runs.

A tool should not care whether IRIS is on the same WiFi as the hardware or on
a VPS on another continent. It asks for a path and some parameters; this picks
the route:

* ``fast`` — one UDP datagram, when the node advertises a port for it
* ``lan``  — an HTTP GET to the device's address (IRIS calls the device)
* ``link`` — a command down the WebSocket the device opened to IRIS

All three answer with a plain dict, and all three raise :class:`ToolError`
with a sentence a person can act on. Everything above this line is identical.

WHY THE UDP PATH EXISTS
Arduino's ``WebServer`` — what every one of these boards runs — serves ONE
client at a time, and holds a socket that has connected but not yet sent its
request for up to five seconds, accepting nothing else meanwhile. Browsers
open exactly such idle sockets, so with the robot's calibration page open in
a tab, a spoken "robot forward" could sit behind it. A datagram has no
handshake and no connection state, so there is nothing for it to queue
behind. The board answers with the same JSON its HTTP route would, and that
reply is what makes the fast path reliable enough to save calibration over:
no answer means fall back to HTTP for that command, not silently lose it.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlsplit

import httpx

from iris.app.core.logging import get_logger
from iris.app.nodes.link import NodeLinkError, NodeLinkHub, default_node_hub
from iris.app.tools.base import ToolError
from iris.app.tools.devices.registry import Device

logger = get_logger("tools.devices.transport")

#: For reads worth waiting for — /status, /sensors — where a slow answer still
#: beats no answer. Six seconds meant a robot that had lost power held the
#: assistant, and the person waiting on it, for six seconds before saying so.
LAN_TIMEOUT = httpx.Timeout(4.0, connect=2.0)

#: For actuator commands — driving the robot, changing the face. A LAN ESP32
#: answers these in single-digit milliseconds, and the turn is awaiting it, so
#: the budget is what a congested 2.4 GHz link plausibly needs rather than
#: what a wedged one might eventually use. Not tighter than this: a false
#: "it did not answer" on a command that actually landed is worse than a
#: command that took a moment.
COMMAND_TIMEOUT = httpx.Timeout(1.5, connect=0.8)

#: Paths that actuate something, and so take the tight budget above.
_ACTUATOR_PATHS = ("/motor", "/tank", "/drive", "/stop", "/test",
                    "/face", "/look", "/blink", "/speak")

#: How long to wait for the node's answering datagram before falling back to
#: HTTP. A LAN round trip to an ESP32 is single-digit milliseconds; the slack
#: is for a board that is mid-way through serving something else, since the
#: fallback will be slower still than waiting a little longer here.
FAST_TIMEOUT = 0.15

#: How long a board that failed to answer a fast-path probe is left alone.
#: Without this, a board that is simply off re-probed on every command and
#: paid TWO full HTTP timeouts per spoken word — the probe and then the
#: command. Short enough that a board which has just booted is picked up on
#: the next thing you say.
FAST_PROBE_RETRY_S = 5.0

#: base_url -> the UDP command port that node advertised, or None for "this
#: board has no fast path". Learned from the node's own /status, never
#: guessed: a board that does not advertise a port never receives a datagram.
_FAST_PORTS: Dict[str, Optional[int]] = {}

#: base_url -> event-loop time before which not to re-probe a board that
#: failed to answer. See FAST_PROBE_RETRY_S.
_FAST_PROBE_HOLD: Dict[str, float] = {}

#: Kinds whose bundled firmware ships a UDP listener — the robot and the S3
#: face/sensor board — and therefore the only ones worth spending a round trip
#: ASKING. Every other kind still gets the fast path the moment one of its own
#: /status answers advertises a port; it just is not interrogated on the off
#: chance, because a board that never had a listener would pay a timeout for
#: every command.
FAST_PROBE_KINDS = ("motor", "face", "sensor")


def forget_fast_paths() -> None:
    """Drop what we have learned about nodes' fast paths.

    For tests, and for the case where a board is re-flashed under the same
    address and gains (or loses) the UDP listener.
    """
    _FAST_PORTS.clear()
    _FAST_PROBE_HOLD.clear()


def read_fast_port(status: Any) -> Optional[int]:
    """Pull the advertised UDP port out of a node's /status, or None."""
    fast = status.get("fast") if isinstance(status, dict) else None
    if not isinstance(fast, dict):
        return None
    try:
        port = int(fast.get("udp"))
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _host_of(base_url: str) -> str:
    return urlsplit(base_url).hostname or ""


def _looks_unresolvable(exc: BaseException) -> bool:
    """True when a connect failure was name resolution, not an absent board."""
    seen = 0
    while exc is not None and seen < 6:
        if isinstance(exc, socket.gaierror):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


class _OneReply(asyncio.DatagramProtocol):
    """Resolves a future with the first datagram that comes back."""

    def __init__(self, future: "asyncio.Future[bytes]") -> None:
        self._future = future

    def datagram_received(self, data: bytes, _addr: Any) -> None:
        if not self._future.done():
            self._future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        # ICMP port-unreachable lands here, which is how a board running
        # older firmware declines the fast path immediately rather than
        # costing us the whole timeout.
        if not self._future.done():
            self._future.set_exception(exc)


async def udp_command(
    host: str, port: int, target: str, timeout: float = FAST_TIMEOUT
) -> Dict[str, Any]:
    """Send one datagram and return the node's answer, parsed.

    ``target`` is exactly what would follow the host in a URL, e.g.
    ``/motor?dir=forward&speed=200``. Raises :class:`OSError` or
    :class:`asyncio.TimeoutError` rather than a ToolError — the caller decides
    whether an unanswered datagram is worth falling back for.
    """
    loop = asyncio.get_running_loop()
    future: "asyncio.Future[bytes]" = loop.create_future()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: _OneReply(future), remote_addr=(host, port)
    )
    try:
        transport.sendto(target.encode("utf-8"))
        raw = await asyncio.wait_for(future, timeout)
    finally:
        transport.close()

    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        return {"response": text[:400]}
    return data if isinstance(data, dict) else {"response": data}


def remember_fast_port(base_url: str, status: Any) -> Optional[int]:
    """Note what a node's /status says about its fast path.

    Called for every /status that passes through, so a board of any kind
    starts getting datagrams as soon as it says it can take them, without
    this module needing to know which firmwares have grown the listener.
    """
    port = read_fast_port(status)
    _FAST_PORTS[base_url] = port
    if port:
        logger.info("%s advertises a UDP command port on %s", base_url, port)
    return port


async def _fast_port(device: Device) -> Optional[int]:
    """This node's UDP command port, asking the node itself at most once."""
    key = device.base_url
    if key in _FAST_PORTS:
        return _FAST_PORTS[key]
    if device.kind not in FAST_PROBE_KINDS:
        return None
    # A board that is off fails this probe, and the command right after it
    # fails too — so without a hold-off, every spoken command paid TWO full
    # HTTP timeouts before the user heard anything. The hold-off is seconds,
    # not permanent: a board that has just booted must not stay written off as
    # slow for the life of the process.
    now = asyncio.get_running_loop().time()
    if now < _FAST_PROBE_HOLD.get(key, 0.0):
        return None
    try:
        status = await lan_get(f"{key}/status")
    except ToolError:
        _FAST_PROBE_HOLD[key] = now + FAST_PROBE_RETRY_S
        return None
    _FAST_PROBE_HOLD.pop(key, None)
    return remember_fast_port(key, status)


def _client(url: str, timeout: httpx.Timeout) -> httpx.AsyncClient:
    """A client for one device call, built as cheaply as the URL allows.

    Constructing an AsyncClient is not free the way it looks: measured at
    43 ms here, of which ~26 ms is reading proxy and netrc settings out of the
    environment and ~16 ms is building an SSLContext from the certifi bundle.
    This ran once per device call — twice for a cold motor command — so a
    "robot forward" spent ~87 ms of pure CPU before a packet left the machine.

    trust_env=False is a correctness fix as much as a speed one: device
    addresses are LAN-only by construction (registry.normalize_base_url), and
    routing them through a configured HTTP proxy could never work.

    The SSL context is skipped only for http://, which is every board this
    ships with. An https device still gets a verified one and pays for it.
    """
    if url.lower().startswith("https://"):
        return httpx.AsyncClient(timeout=timeout, trust_env=False)
    return httpx.AsyncClient(timeout=timeout, trust_env=False, verify=False)


async def lan_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[httpx.Timeout] = None,
) -> Dict[str, Any]:
    """GET a device endpoint, tolerating non-JSON bodies from custom firmware."""
    host = url.split("/", 3)[2] if "//" in url else url
    try:
        async with _client(url, timeout or LAN_TIMEOUT) as client:
            response = await client.get(url, params=_clean(params))
    except httpx.ConnectError as exc:
        # A name that will not resolve is a different problem from a board
        # that is off, and saying "is it powered on?" sends the user to check
        # the wrong thing. .local is the documented happy path here, and it
        # needs mDNS resolution the host may simply not have.
        if _looks_unresolvable(exc):
            raise ToolError(
                f"Could not look up '{host}' on this network. Names ending in "
                f".local need mDNS, which not every machine has — register the "
                f"device by its IP address instead."
            ) from exc
        raise ToolError(
            f"Could not reach the device at {host} — is it powered on and on the same WiFi?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ToolError(f"The device at {host} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"Device request failed: {exc}") from exc

    if response.status_code >= 400:
        raise ToolError(f"The device answered HTTP {response.status_code}: {response.text[:120]}")
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"response": data}
    except ValueError:
        return {"response": response.text[:400]}


async def device_request(
    device: Device,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    hub: Optional[NodeLinkHub] = None,
) -> Dict[str, Any]:
    """Send one command to a device over whichever transport it uses."""
    if not path.startswith("/"):
        path = "/" + path

    if device.linked:
        try:
            return await (hub or default_node_hub).request(device.name, path, _clean(params))
        except NodeLinkError as exc:
            raise ToolError(str(exc)) from exc

    if not device.base_url:
        raise ToolError(f"'{device.name}' has no address on record.")

    clean = _clean(params)
    port = await _fast_port(device)
    if port:
        query = urlencode({k: str(v) for k, v in clean.items()})
        target = f"{path}?{query}" if query else path
        try:
            return await udp_command(_host_of(device.base_url), port, target)
        except (asyncio.TimeoutError, TimeoutError, OSError, ValueError) as exc:
            # One unanswered datagram is not a reason to abandon the fast path
            # for good — but it IS a reason to send THIS command over HTTP.
            # Falling back rather than retrying is what keeps a lost datagram
            # from turning into a lost calibration save.
            logger.debug(
                "fast path to %s did not answer (%s) — falling back to HTTP",
                device.base_url, exc,
            )

    # A command the turn is waiting on gets the tight budget; a reading worth
    # having gets the patient one.
    timeout = COMMAND_TIMEOUT if path in _ACTUATOR_PATHS else LAN_TIMEOUT
    data = await lan_get(f"{device.base_url}{path}", clean, timeout)
    if path == "/status":
        remember_fast_port(device.base_url, data)
    return data


def _clean(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}
