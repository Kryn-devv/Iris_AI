"""Device control endpoints backing the UI devices panel.

Thin wrappers over the same tools the voice/chat pipeline uses, so a button
press in the drawer and "turn on the light" run identical code.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from iris.app.tools.devices.esp32 import (
    DeviceMotorTool,
    DeviceStatusTool,
    DeviceSwitchTool,
    RegisterDeviceTool,
    RemoveDeviceTool,
)
from iris.app.tools.devices.registry import default_device_registry

router = APIRouter(prefix="/api/v1/devices", tags=["Devices"])


class RegisterPayload(BaseModel):
    name: str
    address: str
    kind: str = "generic"
    channel: int = 1


class SwitchPayload(BaseModel):
    state: str  # on | off | toggle


class MotorPayload(BaseModel):
    action: str
    speed: int | None = None
    duration_ms: int | None = None


def _raise_on_failure(result) -> Dict[str, Any]:
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return result.result


@router.get("", summary="List registered devices with live status")
async def list_devices() -> Dict[str, Any]:
    listing = [d.to_dict() for d in default_device_registry.list()]
    status = await DeviceStatusTool().execute()
    online = {d["name"]: d for d in (status.result or {}).get("devices", [])} if status.success else {}
    for entry in listing:
        entry["online"] = online.get(entry["name"], {}).get("online", False)
        entry["status"] = online.get(entry["name"], {}).get("status")
    return {"devices": listing, "count": len(listing)}


@router.post("", summary="Register a device")
async def register_device(payload: RegisterPayload) -> Dict[str, Any]:
    result = await RegisterDeviceTool().execute(
        name=payload.name, address=payload.address, kind=payload.kind, channel=payload.channel
    )
    return _raise_on_failure(result)


@router.delete("/{name}", summary="Remove a device")
async def remove_device(name: str) -> Dict[str, Any]:
    result = await RemoveDeviceTool().execute(name=name)
    return _raise_on_failure(result)


@router.post("/{name}/switch", summary="Turn a device on/off/toggle")
async def switch_device(name: str, payload: SwitchPayload) -> Dict[str, Any]:
    result = await DeviceSwitchTool().execute(device=name, state=payload.state)
    return _raise_on_failure(result)


@router.post("/{name}/motor", summary="Drive a motor device")
async def motor_device(name: str, payload: MotorPayload) -> Dict[str, Any]:
    result = await DeviceMotorTool().execute(
        action=payload.action, device=name, speed=payload.speed, duration_ms=payload.duration_ms
    )
    return _raise_on_failure(result)
