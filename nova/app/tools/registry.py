"""Tool Registry for dynamic tool registration and discovery."""

from typing import Dict, List, Optional
from nova.app.tools.base import BaseTool
from nova.app.schemas.tools import ToolMetadata
from nova.app.core.logging import get_logger

logger = get_logger("tools.registry")


class ToolRegistry:
    """Registry maintaining available tools in NOVA."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a new tool."""
        if tool.name in self._tools:
            logger.warning(f"Overwriting existing registered tool: {tool.name}")
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name} (permission: {tool.permission_level.value})")

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[ToolMetadata]:
        """Return list of metadata for all registered tools."""
        return [tool.get_metadata() for tool in self._tools.values()]

    def is_registered(self, name: str) -> bool:
        """Check if tool name is registered."""
        return name in self._tools


default_tool_registry = ToolRegistry()
