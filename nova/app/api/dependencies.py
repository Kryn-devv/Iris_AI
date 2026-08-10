"""FastAPI Dependency Injection functions."""

from nova.app.agent.kernel import AgentKernel, default_kernel
from nova.app.tools.registry import ToolRegistry, default_tool_registry
from nova.app.llm.gateway import ModelGateway, default_model_gateway
from nova.app.core.security import PermissionManager, default_permission_manager
from nova.app.agent.task_manager import TaskManager


def get_agent_kernel() -> AgentKernel:
    """Dependency providing AgentKernel instance."""
    return default_kernel


def get_tool_registry() -> ToolRegistry:
    """Dependency providing ToolRegistry instance."""
    return default_tool_registry


def get_model_gateway() -> ModelGateway:
    """Dependency providing ModelGateway instance."""
    return default_model_gateway


def get_permission_manager() -> PermissionManager:
    """Dependency providing PermissionManager instance."""
    return default_permission_manager


def get_task_manager() -> TaskManager:
    """Dependency providing TaskManager instance."""
    return default_kernel.task_manager
