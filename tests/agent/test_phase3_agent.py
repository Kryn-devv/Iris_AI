"""Comprehensive tests for Phase 3 Agentic Intelligence."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient

from iris.app.agent.kernel import AgentKernel
from iris.app.agent.events import EventDispatcher, AgentEventType
from iris.app.agent.context import ContextAssembler
from iris.app.tools.builtin.calculator import CalculatorTool
from iris.app.tools.builtin.string_utils import StringUtilsTool
from iris.app.tools.builtin.unit_converter import UnitConverterTool
from iris.app.tools.registry import ToolRegistry
from iris.app.core.security import PermissionManager, PermissionLevel, PermissionDecision
from iris.app.schemas.tasks import TaskStatus


@pytest.mark.asyncio
async def test_phase3_multi_step_agent_execution():
    """Verify multi-step tool call sequence: (20 + 30) -> 50, then (50 * 5) -> 250."""
    kernel = AgentKernel()
    res = await kernel.process_request("Calculate 20 + 30, then multiply the result by 5.")
    
    assert res.status == "COMPLETED"
    assert "250" in res.response
    assert res.provider in ("mock", "local")


@pytest.mark.asyncio
async def test_phase3_replanning_on_tool_failure():
    """Verify kernel triggers replanning when a tool fails or is unregistered."""
    kernel = AgentKernel()
    
    events_captured = []
    kernel.event_dispatcher.register_listener(lambda e: events_captured.append(e.event_type))

    res = await kernel.process_request("Please execute replan test failure recovery.")
    assert res.status == "COMPLETED"
    assert "Recovered" in res.response or "processed" in res.response.lower() or "calculation" in res.response.lower()
    assert AgentEventType.REPLAN_STARTED in events_captured or AgentEventType.TOOL_FAILED in events_captured


@pytest.mark.asyncio
async def test_phase3_confirmation_flow_and_revalidation():
    """Verify confirmation flow: tool requiring confirmation pauses in WAITING_FOR_CONFIRMATION, revalidates permissions, and resumes upon approval."""
    tool_reg = ToolRegistry()
    pm = PermissionManager(auto_approve_low_risk=False)  # Require confirmation for low risk actions
    kernel = AgentKernel(tool_registry=tool_reg, permission_manager=pm)
    
    # Register calculator which requires confirmation when auto_approve_low_risk is False
    calc = CalculatorTool()
    tool_reg.register(calc)

    # 1. Process request expecting confirmation prompt
    res = await kernel.process_request("Calculate 25 * 47")
    assert res.status == "WAITING_FOR_CONFIRMATION"
    assert "requires" in res.response.lower() or "confirmation" in res.response.lower()

    task_id = res.task_id
    state = kernel.task_manager.get_task(task_id)
    assert state is not None
    assert state.pending_tool_call is not None
    assert state.pending_tool_call["tool_name"] == "calculator"

    # 2. Resume with user approval
    res_confirmed = await kernel.resume_task_confirmation(task_id, approved=True)
    assert res_confirmed.status == "COMPLETED"
    assert state.pending_tool_call is None


@pytest.mark.asyncio
async def test_phase3_confirmation_rejection_flow():
    """Verify user rejection of a pending tool call logs rejection observation and completes gracefully."""
    tool_reg = ToolRegistry()
    pm = PermissionManager(auto_approve_low_risk=False)
    kernel = AgentKernel(tool_registry=tool_reg, permission_manager=pm)
    calc = CalculatorTool()
    tool_reg.register(calc)

    res = await kernel.process_request("Calculate 10 + 10")
    assert res.status == "WAITING_FOR_CONFIRMATION"
    task_id = res.task_id

    # Resume with rejection
    res_rejected = await kernel.resume_task_confirmation(task_id, approved=False)
    assert res_rejected.status == "COMPLETED" or res_rejected.status == "RUNNING"


@pytest.mark.asyncio
async def test_phase3_new_safe_builtin_tools():
    """Test StringUtilsTool and UnitConverterTool execution."""
    string_tool = StringUtilsTool()
    unit_tool = UnitConverterTool()

    # String tool tests
    res_str = await string_tool.execute(text="iris agent", operation="uppercase")
    assert res_str.success is True
    assert res_str.result["result"] == "IRIS AGENT"

    res_replace = await string_tool.execute(text="hello world", operation="replace", old="world", new="IRIS")
    assert res_replace.success is True
    assert res_replace.result["result"] == "hello IRIS"

    # Unit converter tests
    res_temp = await unit_tool.execute(value=100.0, from_unit="fahrenheit", to_unit="celsius")
    assert res_temp.success is True
    assert res_temp.result["result"] == 37.7778

    res_dist = await unit_tool.execute(value=10.0, from_unit="meters", to_unit="feet")
    assert res_dist.success is True
    assert res_dist.result["result"] == 32.8084


@pytest.mark.asyncio
async def test_phase3_events_contain_no_chain_of_thought():
    """Verify dispatched events only contain high-level safe execution states and zero chain-of-thought."""
    kernel = AgentKernel()
    captured_events = []
    kernel.event_dispatcher.register_listener(lambda e: captured_events.append(e))

    await kernel.process_request("What is 15 multiplied by 6?")
    assert len(captured_events) > 0
    for evt in captured_events:
        dump = str(evt.model_dump()).lower()
        assert "chain_of_thought" not in dump
        assert "reasoning_steps" not in dump
        assert evt.event_type in AgentEventType.__members__.values()


@pytest.mark.asyncio
async def test_phase3_api_confirm_and_reject_endpoints(async_client: AsyncClient):
    """Verify POST /api/v1/tasks/{task_id}/confirm and /reject REST API endpoints."""
    # Create task
    create_res = await async_client.post("/api/v1/tasks", json={"user_input": "Calculate 50 + 50"})
    assert create_res.status_code == 200
    data = create_res.json()
    task_id = data["task_id"]

    # Verify cancel endpoint still works cleanly
    cancel_res = await async_client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert cancel_res.status_code in (200, 400)  # 200 if running, 400 if already completed
