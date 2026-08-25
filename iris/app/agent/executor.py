"""Execution Engine for validating permissions and executing tools safely."""

from typing import Dict, Any, Optional
from iris.app.tools.registry import ToolRegistry
from iris.app.core.security import PermissionManager, PermissionDecision
from iris.app.schemas.tools import ToolExecutionResult
from iris.app.core.logging import get_logger

logger = get_logger("agent.executor")


class ExecutionEngine:
    """Handles authorization verification and safe execution of tools."""

    def __init__(self, tool_registry: ToolRegistry, permission_manager: PermissionManager):
        self.tool_registry = tool_registry
        self.permission_manager = permission_manager

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_approved: bool = False,
        timeout: float = 10.0,
    ) -> ToolExecutionResult:
        """Verify tool existence, authorization level, and execute."""
        tool = self.tool_registry.get(tool_name)
        if not tool:
            error_msg = f"Tool '{tool_name}' is not registered."
            logger.error(error_msg)
            return ToolExecutionResult(tool_name=tool_name, success=False, error=error_msg)

        # Permission check
        decision = self.permission_manager.evaluate(
            tool_name=tool.name,
            permission_level=tool.permission_level,
            user_approved=user_approved,
        )

        if decision == PermissionDecision.DENIED:
            error_msg = f"Execution of tool '{tool_name}' was DENIED due to security policy."
            logger.warning(error_msg)
            return ToolExecutionResult(tool_name=tool_name, success=False, error=error_msg)

        if decision == PermissionDecision.REQUIRES_CONFIRMATION:
            error_msg = f"Execution of tool '{tool_name}' REQUIRES_CONFIRMATION from user."
            logger.info(error_msg)
            return ToolExecutionResult(tool_name=tool_name, success=False, error=error_msg)

        # Execute tool
        logger.info(f"Executing tool '{tool_name}' with args: {arguments}")
        return await tool.execute(timeout=timeout, **arguments)
