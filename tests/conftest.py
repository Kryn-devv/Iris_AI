"""Pytest configuration and shared fixtures for NOVA tests."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from nova.app.main import app
from nova.app.agent.kernel import AgentKernel
from nova.app.tools.registry import ToolRegistry, default_tool_registry
from nova.app.tools.builtin.calculator import CalculatorTool
from nova.app.tools.builtin.system_info import SystemInfoTool
from nova.app.tools.builtin.time import TimeTool
from nova.app.llm.gateway import ModelGateway
from nova.app.core.security import PermissionManager


@pytest.fixture(autouse=True)
def setup_builtin_tools():
    """Ensure builtin tools are always registered for test runs."""
    default_tool_registry.register(CalculatorTool())
    default_tool_registry.register(SystemInfoTool())
    default_tool_registry.register(TimeTool())


@pytest.fixture
def tool_registry():
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(SystemInfoTool())
    registry.register(TimeTool())
    return registry


@pytest.fixture
def permission_manager():
    return PermissionManager(auto_approve_low_risk=True)


@pytest.fixture
def model_gateway():
    return ModelGateway()


@pytest.fixture
def kernel(model_gateway, tool_registry, permission_manager):
    return AgentKernel(
        model_gateway=model_gateway,
        tool_registry=tool_registry,
        permission_manager=permission_manager,
    )


@pytest_asyncio.fixture
async def async_client():
    # Execute lifespan startup & shutdown for ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
