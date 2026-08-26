"""Pytest configuration and shared fixtures for IRIS tests."""

# Force hermetic tests: a developer's real .env (API keys, custom ports)
# must never leak into the suite. Set before any iris import creates Settings.
import os

os.environ["LLM_MODE"] = "mock"
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("OPENROUTER_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)


import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from iris.app.main import app
from iris.app.agent.kernel import AgentKernel
from iris.app.tools.registry import ToolRegistry, default_tool_registry
from iris.app.tools.builtin.calculator import CalculatorTool
from iris.app.tools.builtin.system_info import SystemInfoTool
from iris.app.tools.builtin.time import TimeTool
from iris.app.llm.gateway import ModelGateway
from iris.app.core.security import PermissionManager


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
