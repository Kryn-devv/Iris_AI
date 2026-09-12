"""Integration: kernel agent loop driving tools through a mocked cloud provider."""

import json

import httpx
import pytest

from iris.app.agent.kernel import AgentKernel
from iris.app.core.config import settings
from iris.app.core.security import PermissionLevel, PermissionManager
from iris.app.llm.cloud import CloudLLMProvider
from iris.app.llm.gateway import ModelGateway, _Circuit
from iris.app.nlu.engine import IntentEngine
from iris.app.schemas.tools import ToolParameterSchema
from iris.app.tools.base import BaseTool
from iris.app.tools.registry import ToolRegistry


class LookupTool(BaseTool):
    name = "lookup_number"
    description = "Return a magic number."
    permission_level = PermissionLevel.READ
    input_schema = ToolParameterSchema(properties={"key": {"type": "string"}})

    async def _run(self, key: str = "", **_):
        return {"value": 1234 if key == "answer" else 0}


def scripted_cloud(name="scripted"):
    """A cloud provider that first requests a tool call, then answers."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
        if not has_tool_result:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "lookup_number",
                                 "arguments": json.dumps({"key": "answer"})},
                }],
            }
        else:
            tool_payload = next(m["content"] for m in body["messages"] if m.get("role") == "tool")
            value = json.loads(tool_payload)["value"]
            message = {"role": "assistant", "content": f"The magic number is {value}."}
        return httpx.Response(200, json={
            "model": "m1",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {},
        })

    provider = CloudLLMProvider(name, "https://fake.test/v1", api_key="k", default_model="m1")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


async def test_agent_loop_function_calling_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODE", "cloud")
    monkeypatch.setattr(settings, "LLM_PROVIDER_ORDER", ["scripted"])

    gateway = ModelGateway()
    provider = scripted_cloud()
    gateway.cloud_providers = {"scripted": provider}
    gateway._circuits = {"scripted": _Circuit()}

    registry = ToolRegistry()
    registry.register(LookupTool(), quiet=True)

    kernel = AgentKernel(
        model_gateway=gateway,
        tool_registry=registry,
        permission_manager=PermissionManager(),
        intent_engine=IntentEngine([]),  # force the agent loop
    )
    res = await kernel.process_request("please tell me the magic number")
    assert res.handler == "agent"
    assert res.status == "COMPLETED"
    assert "1234" in res.response
    assert res.tools_executed[0].tool_name == "lookup_number"
    assert res.provider == "scripted"
    await provider.close()


def scripted_gemini():
    """Gemini's OpenAI layer: the tool call carries a thought signature, and
    the follow-up request is refused unless it comes back on that call."""
    seen = {"second": None}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
        if not has_tool_result:
            message = {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "lookup_number", "arguments": json.dumps({"key": "answer"})},
                    "extra_content": {"google": {"thought_signature": "sig-xyz"}},
                }],
            }
        else:
            seen["second"] = body
            replayed = next(m for m in body["messages"] if m.get("role") == "assistant" and m.get("tool_calls"))
            sig = (replayed["tool_calls"][0].get("extra_content") or {}).get("google", {}).get("thought_signature")
            if not sig:
                return httpx.Response(400, json={"error": {"code": 400, "message":
                    "Function call is missing a thought_signature in functionCall parts."}})
            value = json.loads(next(m["content"] for m in body["messages"] if m.get("role") == "tool"))["value"]
            message = {"role": "assistant", "content": f"The magic number is {value}."}
        return httpx.Response(200, json={"model": "gemini-flash-latest",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}], "usage": {}})

    provider = CloudLLMProvider(
        "gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="k", default_model="gemini-flash-latest",
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider, seen


async def test_agent_loop_replays_gemini_thought_signature(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODE", "cloud")
    monkeypatch.setattr(settings, "LLM_PROVIDER_ORDER", ["gemini"])

    gateway = ModelGateway()
    provider, seen = scripted_gemini()
    gateway.cloud_providers = {"gemini": provider}
    gateway._circuits = {"gemini": _Circuit()}

    registry = ToolRegistry()
    registry.register(LookupTool(), quiet=True)
    kernel = AgentKernel(
        model_gateway=gateway, tool_registry=registry,
        permission_manager=PermissionManager(), intent_engine=IntentEngine([]),
    )
    res = await kernel.process_request("please tell me the magic number")
    assert res.status == "COMPLETED", res.response
    assert "1234" in res.response
    replayed = next(m for m in seen["second"]["messages"] if m.get("role") == "assistant" and m.get("tool_calls"))
    assert replayed["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "sig-xyz"}}
    await provider.close()
