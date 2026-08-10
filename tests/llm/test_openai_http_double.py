"""Test proving real OpenAI HTTP request parsing via a fake OpenAI-compatible server."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from openai import AsyncOpenAI
from nova.app.llm.local import LocalLLMProvider

# Create fake OpenAI-compatible server app
fake_openai_app = FastAPI()


@fake_openai_app.post("/v1/chat/completions")
async def fake_chat_completions(payload: dict):
    return {
        "id": "chatcmpl-fake123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": payload.get("model", "fake-local-model"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello, I am NOVA.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 5,
            "total_tokens": 20,
        },
    }


@fake_openai_app.get("/v1/models")
async def fake_list_models():
    return {
        "object": "list",
        "data": [{"id": "fake-local-model", "object": "model", "owned_by": "nova"}],
    }


@pytest.mark.asyncio
async def test_openai_http_double_chat_completions():
    """Verify LocalLLMProvider issuing real HTTP calls to POST /v1/chat/completions."""
    # Wire LocalLLMProvider to fake_openai_app via ASGITransport
    transport = ASGITransport(app=fake_openai_app)
    custom_http_client = AsyncClient(transport=transport, base_url="http://fake-openai/v1")

    provider = LocalLLMProvider(
        base_url="http://fake-openai/v1",
        api_key="EMPTY",
        default_model="fake-local-model",
    )
    # Inject custom httpx client into AsyncOpenAI
    provider.client = AsyncOpenAI(
        base_url="http://fake-openai/v1",
        api_key="EMPTY",
        http_client=custom_http_client,
    )

    # 1. Health check detailed over HTTP
    health = await provider.health_check_detailed()
    assert health.available is True
    assert health.model == "fake-local-model"

    # 2. Call generate() over HTTP to POST /v1/chat/completions
    response = await provider.generate("Greetings")
    assert response.content == "Hello, I am NOVA."
    assert response.provider_name == "local"
    assert response.model_name == "fake-local-model"
    assert response.prompt_tokens == 15
    assert response.completion_tokens == 5
