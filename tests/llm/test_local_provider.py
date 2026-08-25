"""Tests for LocalLLMProvider, ModelGateway modes, and OpenAI-compatible integration."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from pydantic import BaseModel

from iris.app.llm.local import LocalLLMProvider
from iris.app.llm.gateway import ModelGateway
from iris.app.tools.adapter import ToolSchemaAdapter
from iris.app.tools.builtin.calculator import CalculatorTool
from iris.app.core.config import settings


class DummyResponseModel(BaseModel):
    summary: str
    code: int


@pytest.mark.asyncio
async def test_local_provider_init_and_config():
    provider = LocalLLMProvider(
        base_url="http://test-host:8000/v1",
        api_key="test-key",
        default_model="qwen-2.5",
        timeout=15.0,
    )
    assert provider.provider_name == "local"
    assert provider.base_url == "http://test-host:8000/v1"
    assert provider.api_key == "test-key"
    assert provider.default_model == "qwen-2.5"
    assert provider.timeout == 15.0


@pytest.mark.asyncio
async def test_local_provider_health_check_success():
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1")
    
    mock_models = MagicMock()
    mock_models.list = AsyncMock(return_value=MagicMock(data=[MagicMock(id="local-model-1")]))
    provider.client.models = mock_models

    healthy = await provider.health_check()
    assert healthy is True

    detailed = await provider.health_check_detailed()
    assert detailed.available is True
    assert detailed.provider == "local"
    assert detailed.model == "local-model-1"
    assert detailed.latency_ms is not None
    assert "test-key" not in str(detailed.model_dump())


@pytest.mark.asyncio
async def test_local_provider_health_check_failure():
    provider = LocalLLMProvider(base_url="http://invalid-host:9999/v1")
    
    mock_models = MagicMock()
    mock_models.list = AsyncMock(side_effect=RuntimeError("Connection refused"))
    provider.client.models = mock_models

    healthy = await provider.health_check()
    assert healthy is False

    detailed = await provider.health_check_detailed()
    assert detailed.available is False
    assert detailed.error == "Connection refused"


@pytest.mark.asyncio
async def test_local_provider_generate_success():
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1", default_model="test-model")

    mock_choice = MagicMock()
    mock_choice.message.content = "Hello, I am IRIS."
    mock_response = MagicMock(choices=[mock_choice], usage=MagicMock(prompt_tokens=10, completion_tokens=5))
    
    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(return_value=mock_response)
    provider.client.chat.completions = mock_completions

    res = await provider.generate("Hi")
    assert res.content == "Hello, I am IRIS."
    assert res.provider_name == "local"
    assert res.model_name == "test-model"
    assert res.prompt_tokens == 10
    assert res.completion_tokens == 5


@pytest.mark.asyncio
async def test_local_provider_generate_structured_success():
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1", default_model="test-model")

    mock_choice = MagicMock()
    mock_choice.message.content = '{"summary": "Calculated value", "code": 200}'
    mock_response = MagicMock(choices=[mock_choice], usage=None)
    
    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(return_value=mock_response)
    provider.client.chat.completions = mock_completions

    res = await provider.generate_structured("Summarize task", response_model=DummyResponseModel)
    assert res.summary == "Calculated value"
    assert res.code == 200


@pytest.mark.asyncio
async def test_local_provider_streaming():
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1")

    chunk1 = MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello "))])
    chunk2 = MagicMock(choices=[MagicMock(delta=MagicMock(content="world!"))])

    async def async_generator():
        yield chunk1
        yield chunk2

    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(return_value=async_generator())
    provider.client.chat.completions = mock_completions

    chunks = []
    async for token in provider.stream("Hello"):
        chunks.append(token)

    assert "".join(chunks) == "Hello world!"


def test_tool_schema_adapter():
    calc = CalculatorTool()
    schema = ToolSchemaAdapter.from_tool(calc)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "calculator"
    assert "expression" in schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_model_gateway_auto_fallback(monkeypatch):
    gateway = ModelGateway()
    
    # Force auto mode
    monkeypatch.setattr(settings, "LLM_MODE", "auto")

    # Mock local provider health check failure
    gateway.local_provider.health_check = AsyncMock(return_value=False)

    provider, provider_name = await gateway.get_provider_and_name()
    assert provider_name == "mock"
    assert provider == gateway.mock_provider


@pytest.mark.asyncio
async def test_model_gateway_auto_local_healthy(monkeypatch):
    gateway = ModelGateway()
    
    monkeypatch.setattr(settings, "LLM_MODE", "auto")
    gateway.local_provider.health_check = AsyncMock(return_value=True)

    provider, provider_name = await gateway.get_provider_and_name()
    assert provider_name == "local"
    assert provider == gateway.local_provider


def test_capability_routing():
    gateway = ModelGateway()
    
    model_name, err = gateway.select_model_for_capability("FAST")
    assert err is None
    assert model_name == settings.FAST_MODEL

    model_name, err = gateway.select_model_for_capability("VISION")
    assert err is not None
    assert "unavailable" in err.lower()


@pytest.mark.asyncio
async def test_local_provider_missing_model_config():
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1", default_model=None)
    assert provider.default_model == "local-model"


@pytest.mark.asyncio
async def test_local_provider_generate_connection_refusal():
    from openai import APIConnectionError
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1")
    provider.client.chat.completions.create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))

    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate("Hello")
    assert "unavailable" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_local_provider_generate_timeout():
    from openai import APITimeoutError
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1")
    provider.client.chat.completions.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))

    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate("Hello")
    assert "unavailable" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_local_provider_generate_structured_invalid_json():
    provider = LocalLLMProvider(base_url="http://localhost:8000/v1")

    mock_choice = MagicMock()
    mock_choice.message.content = 'Not valid JSON'
    mock_response = MagicMock(choices=[mock_choice], usage=None)
    
    provider.client.chat.completions.create = AsyncMock(return_value=mock_response)

    with pytest.raises(ValueError) as exc_info:
        await provider.generate_structured("Summarize task", response_model=DummyResponseModel)
    assert "invalid json" in str(exc_info.value).lower()

