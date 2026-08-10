"""System Information tool for retrieving safe diagnostic information."""

import sys
import platform
from typing import Any, Dict
import psutil

from nova.app.tools.base import BaseTool
from nova.app.core.security import PermissionLevel
from nova.app.schemas.tools import ToolParameterSchema


class SystemInfoTool(BaseTool):
    """Tool for gathering safe system diagnostics."""

    name = "system_info"
    description = "Provides safe diagnostic information about operating system, CPU, memory, and Python runtime."
    permission_level = PermissionLevel.READ
    input_schema = ToolParameterSchema(
        type="object",
        properties={},
        required=[],
    )

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        mem = psutil.virtual_memory()
        cpu_count_logical = psutil.cpu_count(logical=True)
        cpu_count_physical = psutil.cpu_count(logical=False)

        return {
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "python_version": sys.version.split(" ")[0],
            "cpu": {
                "logical_cores": cpu_count_logical,
                "physical_cores": cpu_count_physical,
            },
            "memory": {
                "total_gb": round(mem.total / (1024 ** 3), 2),
                "available_gb": round(mem.available / (1024 ** 3), 2),
                "percent_used": mem.percent,
            },
        }
