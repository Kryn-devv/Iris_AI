"""Tests for LLM abstraction layer and MockLLMProvider."""

import pytest
from iris.app.llm.mock import MockLLMProvider
from iris.app.llm.gateway import ModelGateway
from iris.app.schemas.agent import AgentPlan


@pytest.mark.asyncio
async def test_mock_llm_calculator_intent():
    provider = MockLLMProvider()
    plan = await provider.generate_structured("What is 25 multiplied by 47?", response_model=AgentPlan)
    assert plan.user_intent == "calculator"
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "calculator"
    assert plan.steps[0].tool_args["expression"] == "25 * 47"


@pytest.mark.asyncio
async def test_mock_llm_system_info_intent():
    provider = MockLLMProvider()
    plan = await provider.generate_structured("What operating system am I running?", response_model=AgentPlan)
    assert plan.user_intent == "system_info"
    assert plan.steps[0].tool_name == "system_info"


@pytest.mark.asyncio
async def test_mock_llm_time_intent():
    provider = MockLLMProvider()
    plan = await provider.generate_structured("What time is it?", response_model=AgentPlan)
    assert plan.user_intent == "time"
    assert plan.steps[0].tool_name == "time"


@pytest.mark.asyncio
async def test_mock_llm_unsupported_intent():
    provider = MockLLMProvider()
    res = await provider.generate("Control the physical humanoid robot arms")
    assert "real LLM provider" in res.content
    assert "not connected yet" in res.content


def test_model_gateway_routing():
    gateway = ModelGateway()
    mock_provider = gateway.get_provider("mock")
    assert mock_provider.provider_name == "mock"

    # Capability tags
    assert gateway.select_model("FAST") == "mock-fast"
    assert gateway.select_model("REASONING") == "mock-reasoning"
