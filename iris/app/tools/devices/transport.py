"""One way to talk to a device, whichever direction the connection runs.

A tool should not care whether IRIS is on the same WiFi as the hardware or on
a VPS on another continent. It asks for a path and some parameters; this picks
the route:

* ``lan``  — an HTTP GET to the device's address (IRIS calls the device)
* ``link`` — a command down the WebSocket the device opened to IRIS

Both answer with a plain dict, and both raise :class:`ToolError` with a
sentence a person can act on. Everything above this line is identical.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from iris.app.core.logging import get_logger
from iris.app.nodes.link import NodeLinkError, NodeLinkHub, default_node_hub
from iris.app.tools.base import ToolError
from iris.app.tools.devices.registry import Device

logger = get_logger("tools.devices.transport")

#: ESP32 web servers answer in well under a second on a healthy LAN.
LAN_TIMEOUT = httpx.Timeout(6.0, connect=3.0)


async def lan_get(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET a device endpoint, tolerating non-JSON bodies from custom firmware."""
    host = url.split("/", 3)[2] if "//" in url else url
    try:
        async with httpx.AsyncClient(timeout=LAN_TIMEOUT) as client:
            response = await client.get(url, params=_clean(params))
    except httpx.ConnectError as exc:
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
    return await lan_get(f"{device.base_url}{path}", params)


def _clean(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}
