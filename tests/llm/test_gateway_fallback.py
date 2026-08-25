"""Tests for the cloud provider and the gateway's fallback chain."""

import json

import httpx
import pytest

from iris.app.core.config import settings
from iris.app.llm.base import LLMProviderError
from iris.app.llm.cloud import CloudLLMProvider, extract_json_object
from iris.app.llm.gateway import ModelGateway


def make_provider(handler, name="prov", key="k-123") -> CloudLLMProvider:
    provider = CloudLLMProvider(
        provider_name=name, base_url="https://fake.test/v1", api_key=key, default_model="m1"
    )
    transport = httpx.MockTransport(handler)
    provider._client = httpx.AsyncClient(transport=transport)
    return provider


def ok_completion(content="hello", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "id": "x",
            "model": "m1",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        },
    )


# ------------------------------------------------------------------ provider
async def test_generate_parses_response():
    provider = make_provider(lambda req: ok_completion("hi there"))
    res = await provider.generate("hello")
    assert res.content == "hi there"
    assert res.prompt_tokens == 5
    assert res.provider_name == "prov"
    await provider.close()


async def test_generate_extracts_tool_calls():
    calls = [{
        "id": "c1", "type": "function",
        "function": {"name": "open_website", "arguments": json.dumps({"site": "youtube"})},
    }]
    provider = make_provider(lambda req: ok_completion("", tool_calls=calls))
    res = await provider.generate("open youtube", tools=[{"type": "function"}])
    assert res.tool_calls[0]["function"]["name"] == "open_website"
    await provider.close()


async def test_http_429_is_retryable_error():
    provider = make_provider(lambda req: httpx.Response(429, json={"error": "rate limited"}))
    with pytest.raises(LLMProviderError) as err:
        await provider.generate("x")
    assert err.value.retryable is True
    assert err.value.status_code == 429
    await provider.close()


async def test_http_401_not_retryable():
    provider = make_provider(lambda req: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(LLMProviderError) as err:
        await provider.generate("x")
    assert err.value.retryable is False
    await provider.close()


async def test_error_bodies_are_redacted():
    provider = make_provider(lambda req: httpx.Response(500, text="key k-123 leaked"))
    with pytest.raises(LLMProviderError) as err:
        await provider.generate("x")
    assert "k-123" not in str(err.value)
    await provider.close()


async def test_unconfigured_provider_refuses():
    provider = CloudLLMProvider("p", "https://x.test/v1", api_key=None, default_model="m")
    with pytest.raises(LLMProviderError) as err:
        await provider.generate("x")
    assert err.value.retryable is False


async def test_structured_generation_retries_and_validates():
    from pydantic import BaseModel

    class Shape(BaseModel):
        a: int

    attempts = {"n": 0}

    def handler(req):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return ok_completion("not json at all")
        return ok_completion('```json\n{"a": 42}\n```')

    provider = make_provider(handler)
    result = await provider.generate_structured("gimme", Shape)
    assert result.a == 42
    assert attempts["n"] == 2
    await provider.close()


def test_extract_json_object_variants():
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    assert extract_json_object("```json\n{\"x\": true}\n```") == {"x": True}
    assert extract_json_object("no json here") is None


# ------------------------------------------------------------------- gateway
async def test_gateway_falls_back_to_next_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODE", "cloud")
    gateway = ModelGateway()

    failing = make_provider(lambda req: httpx.Response(503, text="down"), name="one")
    working = make_provider(lambda req: ok_completion("from two"), name="two")
    gateway.cloud_providers = {"one": failing, "two": working}
    from iris.app.llm.gateway import _Circuit
    gateway._circuits = {"one": _Circuit(), "two": _Circuit()}
    monkeypatch.setattr(settings, "LLM_PROVIDER_ORDER", ["one", "two"])

    res = await gateway.generate("hi")
    assert res.content == "from two"
    assert res.provider_name == "two"
    # Circuit for the failing provider is now open.
    assert gateway._circuits["one"].is_open
    await failing.close(); await working.close()


async def test_gateway_falls_back_to_mock_when_all_fail(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODE", "cloud")
    gateway = ModelGateway()
    failing = make_provider(lambda req: httpx.Response(500, text="boom"), name="one")
    gateway.cloud_providers = {"one": failing}
    from iris.app.llm.gateway import _Circuit
    gateway._circuits = {"one": _Circuit()}
    monkeypatch.setattr(settings, "LLM_PROVIDER_ORDER", ["one"])

    res = await gateway.generate("hello there")
    assert res.provider_name == "mock"
    await failing.close()


async def test_gateway_mock_mode_never_calls_cloud(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODE", "mock")
    gateway = ModelGateway()

    def explode(req):
        raise AssertionError("cloud should not be called in mock mode")

    gateway.cloud_providers = {"one": make_provider(explode, name="one")}
    res = await gateway.generate("hello")
    assert res.provider_name == "mock"
