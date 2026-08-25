"""Network information and connectivity tools.

* :class:`NetworkInfoTool` — "what's my IP address?", "am I online?"
* :class:`PingTool` — "ping google.com", "is the NAS reachable?"

Design notes:

* The "default route" local IP is discovered with the classic UDP-connect
  trick: connecting a datagram socket to a public address sends **no packets**
  but lets the kernel pick the outbound interface, whose address
  ``getsockname()`` then reveals.
* Internet reachability is a plain TCP connect to ``1.1.1.1:443`` with a 2 s
  timeout — no DNS involved, so a broken resolver doesn't masquerade as a
  dead uplink.
* The public IP is looked up (via ``https://api.ipify.org``) **only** when the
  caller explicitly passes ``public=True`` — it leaks the user's address to a
  third-party service, so it must be opt-in.
* :class:`PingTool` shells out to the system ``ping`` binary, but the host is
  validated against a strict hostname/IP grammar first (:func:`validate_host`)
  and the argv is built as a list — spaces, semicolons and other shell
  metacharacters can never smuggle anything past the validator.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from typing import Any

import httpx

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import has_binary, is_windows, try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.system.network")

__all__ = [
    "validate_host",
    "build_ping_args",
    "parse_ping_output",
    "NetworkInfoTool",
    "PingTool",
    "get_tools",
]

#: Reachability probe target: Cloudflare DNS over TCP 443, 2 second budget.
_REACHABILITY_HOST = "1.1.1.1"
_REACHABILITY_PORT = 443
_REACHABILITY_TIMEOUT = 2.0

#: Public IP echo service (returns the caller's address as plain text).
_PUBLIC_IP_URL = "https://api.ipify.org"
_PUBLIC_IP_TIMEOUT = 5.0

#: Ping defaults.
DEFAULT_PING_COUNT = 4
MAX_PING_COUNT = 10
PING_TIMEOUT = 15.0

#: RFC-952/1123-ish hostname: dot-separated labels of letters, digits and
#: hyphens; no label starts/ends with a hyphen. Deliberately excludes spaces,
#: slashes, semicolons, quotes and every other shell metacharacter.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.?$"
)


# =============================================================================
# Pure helpers
# =============================================================================


def validate_host(host: str) -> str:
    """Validate and normalize a ping target, raising :class:`ToolError`.

    Accepts hostnames (``nas.local``), IPv4 (``8.8.8.8``) and IPv6 (``::1``)
    literals. Anything containing whitespace or shell metacharacters is
    refused — the argv is built as a list anyway, but defence in depth costs
    one regex.
    """
    text = (host or "").strip()
    if not text:
        raise ToolError("A host to ping is required.", speech="Which host should I ping?")

    # IP literal (v4 or v6, brackets tolerated)?
    try:
        ipaddress.ip_address(text.strip("[]"))
        return text.strip("[]")
    except ValueError:
        pass

    if not _HOSTNAME_RE.match(text):
        raise ToolError(
            f"'{text}' is not a valid hostname or IP address.",
            speech="That doesn't look like a valid host name.",
        )
    return text


def build_ping_args(host: str, count: int, *, windows: bool | None = None) -> list[str]:
    """Build the ping argv for the current (or given) platform.

    Windows counts echoes with ``-n``; every POSIX ping uses ``-c``. The host
    goes last as its own argv element, so it is never shell-interpreted.
    """
    if windows is None:
        windows = is_windows()
    count_flag = "-n" if windows else "-c"
    return ["ping", count_flag, str(count), host]


def clamp_count(raw: Any) -> int:
    """Coerce the ping ``count`` argument into ``1..MAX_PING_COUNT``."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PING_COUNT
    return max(1, min(value, MAX_PING_COUNT))


def parse_ping_output(output: str) -> dict[str, Any]:
    """Extract average latency and packet loss from ping's text output.

    Understands the three major formats:

    * Linux:   ``rtt min/avg/max/mdev = 14.3/15.1/16.0/0.7 ms``
    * macOS:   ``round-trip min/avg/max/stddev = 14.3/15.1/16.0/0.7 ms``
    * Windows: ``Minimum = 14ms, Maximum = 16ms, Average = 15ms``

    Returns ``{"avg_ms": float | None, "packet_loss_percent": float | None}``;
    missing values stay ``None`` rather than guessing.
    """
    avg_ms: float | None = None
    loss: float | None = None

    match = re.search(r"min/avg/max[^=]*=\s*[\d.]+/([\d.]+)/", output)
    if match:
        avg_ms = float(match.group(1))
    else:
        match = re.search(r"Average\s*=\s*(\d+)\s*ms", output, re.IGNORECASE)
        if match:
            avg_ms = float(match.group(1))

    match = re.search(r"([\d.]+)%\s*(?:packet\s*)?loss", output, re.IGNORECASE)
    if match:
        try:
            loss = float(match.group(1))
        except ValueError:  # pragma: no cover - regex guarantees a number
            loss = None

    return {"avg_ms": avg_ms, "packet_loss_percent": loss}


# =============================================================================
# Blocking socket probes (always run via ``to_thread``)
# =============================================================================


def _primary_local_ip() -> str | None:
    """Local IP of the default-route interface via the UDP-connect trick.

    ``connect()`` on a datagram socket only sets the destination — nothing is
    transmitted — but it forces the kernel to choose the outbound interface.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(1.0)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def _check_internet(
    host: str = _REACHABILITY_HOST,
    port: int = _REACHABILITY_PORT,
    timeout: float = _REACHABILITY_TIMEOUT,
) -> bool:
    """True when a TCP connection to ``host:port`` succeeds within ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _interface_ips(psutil_mod: Any) -> dict[str, str]:
    """IPv4 address per interface, loopback excluded."""
    result: dict[str, str] = {}
    try:
        addr_map = psutil_mod.net_if_addrs()
    except Exception:  # noqa: BLE001 - permission or platform quirk
        return result
    for interface, addresses in addr_map.items():
        if interface.lower().startswith(("lo", "loopback")):
            continue
        for addr in addresses:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                result[interface] = addr.address
                break
    return result


async def _fetch_public_ip() -> str:
    """Ask api.ipify.org for the public address. Raises :class:`ToolError`."""
    try:
        async with httpx.AsyncClient(timeout=_PUBLIC_IP_TIMEOUT) as client:
            response = await client.get(_PUBLIC_IP_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError(
            f"Couldn't look up the public IP: {exc}",
            speech="I couldn't reach the public IP lookup service.",
        ) from exc

    text = response.text.strip()
    try:
        ipaddress.ip_address(text)
    except ValueError as exc:
        raise ToolError(
            "The public IP service returned an unexpected response.",
            speech="The public IP lookup gave me a strange answer.",
        ) from exc
    return text


async def _run_ping_process(argv: list[str], timeout: float = PING_TIMEOUT) -> tuple[int, str]:
    """Run the ping argv, returning ``(exit_code, combined_output)``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise ToolError(
            "The 'ping' command is not available on this system.",
            speech="I couldn't find the ping command on this machine.",
        ) from None

    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise ToolError(
            f"Ping timed out after {timeout:.0f} seconds.",
            speech="The ping took too long, so I stopped it.",
        ) from None

    output = stdout_bytes.decode("utf-8", errors="replace")
    return (proc.returncode if proc.returncode is not None else -1, output)


# =============================================================================
# Tools
# =============================================================================


class NetworkInfoTool(BaseTool):
    """Report local IPs, hostname, and internet reachability."""

    name = "network_info"
    description = "Shows local IP addresses per interface, hostname and whether the internet is reachable."
    permission_level = PermissionLevel.READ
    category = ToolCategory.SYSTEM
    aliases = ("ip_address", "my_ip", "network_status")
    network = True
    examples = (
        ToolExample(utterance="what's my IP address?", arguments={}),
        ToolExample(utterance="am I connected to the internet?", arguments={}),
        ToolExample(utterance="what's my public IP?", arguments={"public": True}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "public": {
                "type": "boolean",
                "description": "Also look up the public IP via api.ipify.org (opt-in; "
                "sends a request to a third-party service).",
            },
        },
        required=[],
    )

    async def _run(self, public: bool = False, **kwargs: Any) -> dict[str, Any]:
        psutil_mod = try_import("psutil")

        try:
            hostname = socket.gethostname()
        except OSError:  # pragma: no cover - practically impossible
            hostname = "unknown"

        interfaces: dict[str, str] = {}
        if psutil_mod is not None:
            interfaces = await self.to_thread(_interface_ips, psutil_mod)

        primary_ip = await self.to_thread(_primary_local_ip)
        online = await self.to_thread(_check_internet)

        result: dict[str, Any] = {
            "hostname": hostname,
            "interfaces": interfaces,
            "primary_ip": primary_ip,
            "internet_reachable": online,
        }
        if psutil_mod is None:
            result["note"] = "psutil is not installed, so per-interface addresses are unavailable."

        if public:
            try:
                result["public_ip"] = await _fetch_public_ip()
            except ToolError as exc:
                result["public_ip"] = None
                result["public_ip_error"] = str(exc)

        best_ip = primary_ip or next(iter(interfaces.values()), None)
        status = "online" if online else "offline"
        if result.get("public_ip"):
            speech = (
                f"You're {status}. Local IP {best_ip or 'unknown'}, "
                f"public IP {result['public_ip']}."
            )
        elif best_ip:
            speech = f"You're {status}. Your local IP address is {best_ip}."
        else:
            speech = f"You appear to be {status}, and I couldn't find a local IP address."

        lines = [f"Hostname: {hostname}", f"Internet: {'reachable' if online else 'unreachable'}"]
        if primary_ip:
            lines.append(f"Primary IP (default route): {primary_ip}")
        for interface, address in sorted(interfaces.items()):
            lines.append(f"  {interface}: {address}")
        if result.get("public_ip"):
            lines.append(f"Public IP: {result['public_ip']}")
        elif public:
            lines.append(f"Public IP lookup failed: {result.get('public_ip_error', 'unknown error')}")

        result["speech"] = speech
        result["display"] = "\n".join(lines)
        return result


class PingTool(BaseTool):
    """Ping a host with the system ping binary and report latency."""

    name = "ping"
    description = "Pings a host to check whether it is reachable and how fast it responds."
    permission_level = PermissionLevel.NETWORK_ACTION
    category = ToolCategory.SYSTEM
    aliases = ("ping_host", "check_host", "is_up")
    network = True
    examples = (
        ToolExample(utterance="ping google.com", arguments={"host": "google.com"}),
        ToolExample(
            utterance="ping the router twice",
            arguments={"host": "192.168.1.1", "count": 2},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "host": {
                "type": "string",
                "description": "Hostname or IP address to ping, e.g. 'google.com' or '8.8.8.8'.",
            },
            "count": {
                "type": "integer",
                "description": f"Echo requests to send (default {DEFAULT_PING_COUNT}, max {MAX_PING_COUNT}).",
            },
        },
        required=["host"],
    )

    async def _run(self, host: str = "", count: int = DEFAULT_PING_COUNT, **kwargs: Any) -> dict[str, Any]:
        target = validate_host(host)
        echoes = clamp_count(count)

        if not has_binary("ping"):
            raise ToolError(
                "The 'ping' command is not available on this system.",
                speech="I couldn't find the ping command on this machine.",
            )

        argv = build_ping_args(target, echoes)
        logger.info("ping executing %r", argv)
        exit_code, output = await _run_ping_process(argv)

        stats = parse_ping_output(output)
        reachable = exit_code == 0
        avg_ms = stats["avg_ms"]
        loss = stats["packet_loss_percent"]

        if reachable and avg_ms is not None:
            speech = f"{target} is reachable — average latency {avg_ms:.0f} milliseconds."
        elif reachable:
            speech = f"{target} is reachable."
        else:
            speech = f"{target} did not respond to ping."

        return {
            "host": target,
            "count": echoes,
            "reachable": reachable,
            "exit_code": exit_code,
            "avg_ms": avg_ms,
            "packet_loss_percent": loss,
            "output": output[-4000:],
            "speech": speech,
            "display": output.strip()[-4000:],
        }


def get_tools() -> list[BaseTool]:
    return [NetworkInfoTool(), PingTool()]
