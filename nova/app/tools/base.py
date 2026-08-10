"""Abstract Base Class for NOVA Tools."""

import time
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from nova.app.core.security import PermissionLevel
from nova.app.schemas.tools import ToolMetadata, ToolParameterSchema, ToolExecutionResult
from nova.app.core.logging import get_logger

logger = get_logger("tools.base")


class BaseTool(ABC):
    """Abstract interface for all executable tools in NOVA."""

    name: str
    description: str
    permission_level: PermissionLevel
    input_schema: ToolParameterSchema

    @abstractmethod
    async def _run(self, **kwargs: Any) -> Any:
        """Core execution implementation for the tool."""
        pass

    async def execute(self, timeout: float = 10.0, **kwargs: Any) -> ToolExecutionResult:
        """Safely execute the tool with error catching and timeout protection."""
        start_time = time.perf_counter()
        try:
            # Enforce execution timeout
            result = await asyncio.wait_for(self._run(**kwargs), timeout=timeout)
            exec_time = time.perf_counter() - start_time
            return ToolExecutionResult(
                tool_name=self.name,
                success=True,
                result=result,
                execution_time_seconds=exec_time,
            )
        except asyncio.TimeoutError:
            exec_time = time.perf_counter() - start_time
            logger.error(f"Tool '{self.name}' timed out after {timeout} seconds.")
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=f"Execution timed out after {timeout} seconds.",
                execution_time_seconds=exec_time,
            )
        except Exception as e:
            exec_time = time.perf_counter() - start_time
            logger.error(f"Tool '{self.name}' execution error: {e}", exc_info=True)
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=str(e),
                execution_time_seconds=exec_time,
            )

    def get_metadata(self) -> ToolMetadata:
        """Return tool metadata for discovery."""
        return ToolMetadata(
            name=self.name,
            description=self.description,
            permission_level=self.permission_level,
            input_schema=self.input_schema,
        )
