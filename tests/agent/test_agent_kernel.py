"""Tests for AgentKernel orchestrator logic."""

import pytest
from iris.app.agent.kernel import AgentKernel
from iris.app.schemas.tasks import TaskStatus


@pytest.mark.asyncio
async def test_kernel_calculator_flow(kernel: AgentKernel):
    res = await kernel.process_request("What is 25 multiplied by 47?")
    assert res.status == "COMPLETED"
    assert res.intent_detected == "calculator"
    assert len(res.tools_executed) == 1
    assert res.tools_executed[0].tool_name == "calculator"
    assert "1175" in res.response


@pytest.mark.asyncio
async def test_kernel_system_info_flow(kernel: AgentKernel):
    res = await kernel.process_request("What operating system am I running?")
    assert res.status == "COMPLETED"
    assert res.intent_detected == "system_info"
    assert len(res.tools_executed) == 1
    assert res.tools_executed[0].tool_name == "system_info"


@pytest.mark.asyncio
async def test_kernel_time_flow(kernel: AgentKernel):
    res = await kernel.process_request("What time is it?")
    assert res.status == "COMPLETED"
    assert res.intent_detected == "time"
    assert len(res.tools_executed) == 1
    assert res.tools_executed[0].tool_name == "time"


@pytest.mark.asyncio
async def test_kernel_unsupported_query_flow(kernel: AgentKernel):
    res = await kernel.process_request("Can you drive my car?")
    assert res.status == "COMPLETED"
    # Unmatched requests flow through the agent loop and still answer gracefully.
    assert res.handler in ("agent", "smalltalk")
    assert res.response
    assert len(res.tools_executed) == 0
    assert "real LLM provider" in res.response
