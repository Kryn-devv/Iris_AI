"""System Information tool for retrieving safe diagnostic information."""

import sys
import platform
from typing import Any, Dict
import psutil

from iris.app.tools.base import BaseTool
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema


class SystemInfoTool(BaseTool):
    """Tool for gathering safe system diagnostics."""

    name = "system_info"
    description = "Provides safe diagnostic information about operating system, CPU, memory, disk, battery, and Python runtime."
    permission_level = PermissionLevel.READ
    category = ToolCategory.SYSTEM
    aliases = ("pc_specs", "computer_info", "specs", "system_status")
    examples = (ToolExample(utterance="show my system info", arguments={}),)
    input_schema = ToolParameterSchema(
        type="object",
        properties={},
        required=[],
    )

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        mem = psutil.virtual_memory()
        cpu_count_logical = psutil.cpu_count(logical=True)
        cpu_count_physical = psutil.cpu_count(logical=False)

        cpu_percent = psutil.cpu_percent(interval=0.1)
        disk = psutil.disk_usage("/") if platform.system() != "Windows" else psutil.disk_usage("C:\\")
        battery = None
        try:
            batt = psutil.sensors_battery()
            if batt is not None:
                battery = {"percent": batt.percent, "plugged_in": batt.power_plugged}
        except (AttributeError, NotImplementedError):
            battery = None

        speech = (
            f"{platform.system()} with {cpu_count_logical} cores at {cpu_percent:.0f} percent CPU, "
            f"{mem.percent:.0f} percent memory used"
        )
        if battery:
            speech += f", battery at {battery['percent']:.0f} percent"
        speech += "."

        display = (
            f"💻 {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"CPU: {cpu_count_logical} logical / {cpu_count_physical or '?'} physical cores — {cpu_percent:.0f}% busy\n"
            f"Memory: {round(mem.total / (1024 ** 3), 1)} GB total, {mem.percent:.0f}% used\n"
            f"Disk: {round(disk.total / (1024 ** 3))} GB total, {disk.percent:.0f}% used"
        )
        if battery:
            display += f"\nBattery: {battery['percent']:.0f}%{' (charging)' if battery['plugged_in'] else ''}"

        return {
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "python_version": sys.version.split(" ")[0],
            "cpu": {
                "logical_cores": cpu_count_logical,
                "physical_cores": cpu_count_physical,
                "percent": cpu_percent,
            },
            "memory": {
                "total_gb": round(mem.total / (1024 ** 3), 2),
                "available_gb": round(mem.available / (1024 ** 3), 2),
                "percent_used": mem.percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024 ** 3), 2),
                "free_gb": round(disk.free / (1024 ** 3), 2),
                "percent_used": disk.percent,
            },
            "battery": battery,
            "speech": speech,
            "display": display,
        }
