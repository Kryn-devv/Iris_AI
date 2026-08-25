"""System endpoints: capabilities, live metrics, reminders and pairing."""

from __future__ import annotations

import io
import socket
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from iris.app.core import paths
from iris.app.core.auth import ensure_token
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import platform_report, try_import
from iris.app.services.scheduler import default_scheduler_service

router = APIRouter(prefix="/api/v1/system", tags=["System"])
logger = get_logger("api.system")


@router.get("/capabilities", summary="Platform and optional-dependency report")
async def capabilities() -> Dict[str, Any]:
    """What this installation can and cannot do, with install hints."""
    return platform_report().to_dict()


@router.get("/paths", summary="Where IRIS stores things")
async def storage_paths() -> Dict[str, str]:
    return {name: str(path) for name, path in paths.ensure_dirs().items()}


@router.get("/metrics", summary="Live host metrics for the dashboard")
async def metrics() -> Dict[str, Any]:
    psutil = try_import("psutil")
    if psutil is None:
        return {"available": False}
    memory = psutil.virtual_memory()
    battery = None
    try:
        batt = psutil.sensors_battery()
        if batt is not None:
            battery = {"percent": batt.percent, "plugged_in": batt.power_plugged}
    except (AttributeError, NotImplementedError):
        battery = None
    return {
        "available": True,
        "cpu_percent": psutil.cpu_percent(interval=0.0),
        "memory_percent": memory.percent,
        "memory_used_gb": round(memory.used / (1024 ** 3), 1),
        "memory_total_gb": round(memory.total / (1024 ** 3), 1),
        "battery": battery,
    }


@router.get("/reminders", summary="Scheduled reminders, timers and routines")
async def reminders(include_done: bool = False) -> Dict[str, Any]:
    items = await default_scheduler_service.list_scheduled(include_done=include_done)
    return {"reminders": items, "count": len(items)}


@router.delete("/reminders/{reminder_id}", summary="Cancel a scheduled item")
async def cancel_reminder(reminder_id: str) -> Dict[str, Any]:
    ok = await default_scheduler_service.cancel(reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="No active reminder with that id.")
    return {"cancelled": reminder_id}


def _lan_ip() -> str:
    """Best-guess LAN IP of this machine (no packets are actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


@router.get("/pair", summary="Phone pairing info (URL + token)")
async def pair() -> Dict[str, Any]:
    """Everything a phone on the same Wi-Fi needs to connect to IRIS."""
    token = ensure_token()
    lan_ip = _lan_ip()
    url = f"http://{lan_ip}:{settings.PORT}/?token={token}"
    return {
        "lan_ip": lan_ip,
        "port": settings.PORT,
        "url": url,
        "token": token,
        "lan_access_enabled": settings.ALLOW_LAN_ACCESS,
        "note": (
            "Open this URL in your phone's browser (same Wi-Fi). "
            + ("" if settings.ALLOW_LAN_ACCESS else "Set ALLOW_LAN_ACCESS=true in .env first, then restart IRIS.")
        ).strip(),
    }


@router.get("/pair/qr", summary="Pairing QR code (PNG)")
async def pair_qr() -> Response:
    """QR code for the pairing URL (requires the optional qrcode package)."""
    qrcode = try_import("qrcode")
    if qrcode is None:
        raise HTTPException(status_code=503, detail="Install the 'qrcode[pil]' package for QR pairing.")
    token = ensure_token()
    url = f"http://{_lan_ip()}:{settings.PORT}/?token={token}"
    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@router.get("/artifact", summary="Download a generated file (sandboxed)")
async def artifact(path: str) -> Any:
    """Serve a file IRIS produced, restricted to the sandbox roots."""
    from fastapi.responses import FileResponse

    from iris.app.core.security import SandboxError, default_path_sandbox

    try:
        resolved = default_path_sandbox.resolve(path, must_exist=True)
    except (SandboxError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Not a file.")
    return FileResponse(str(resolved), filename=resolved.name)
