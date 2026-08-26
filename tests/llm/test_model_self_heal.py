"""Tests for self-healing model resolution when catalogs rotate.

Free-tier providers retire models every few months; a hardcoded default then
404s forever while the UI silently falls back offline. These tests pin the
recovery behaviour: detect the model_not_found error, pick the best live chat
model from /models, retry once, and remember the replacement.
"""

from __future__ import annotations

import json

import httpx
import pytest

from iris.app.llm.base import LLMProviderError
from iris.app.llm.cloud import CloudLLMProvider, score_chat_model


class TestScoreChatModel:
    def test_excludes_non_chat_models(self):
        for bad in (
            "whisper-large-v3", "meta-llama/llama-prompt-guard-2-22m",
            "openai/gpt-oss-safeguard-20b", "canopylabs/orpheus-v1-english",
            "nvidia/nemotron-3.5-content-safety:free", "text-embedding-3-small",
        ):
            assert score_chat_model(bad) < 0, bad

    def test_prefers_large_known_chat_families(self):
        catalog = [
            "allam-2-7b", "whisper-large-v3", "openai/gpt-oss-120b",
            "qwen/qwen3.6-27b", "groq/compound", "meta-llama/llama-prompt-guard-2-86m",
        ]
        best = max(catalog, key=score_chat_model)
        assert best == "openai/gpt-oss-120b"

    def test_free_suffix_breaks_ties(self):
        assert score_chat_model("z-ai/glm-5.2:free") > score_chat_model("z-ai/glm-5.2")


def _provider_with_transport(handler) -> CloudLLMProvider:
    provider = CloudLLMProvider(
        provider_name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="gsk_test",
        default_model="llama-3.3-70b-versatile",   # retired model
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def _chat_ok(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "model": model,
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })


class TestSelfHeal:
    @pytest.mark.asyncio
    async def test_missing_model_heals_and_remembers(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [
                    {"id": "whisper-large-v3"},
                    {"id": "openai/gpt-oss-120b"},
                    {"id": "allam-2-7b"},
                ]})
            body = json.loads(request.content)
            if body["model"] == "llama-3.3-70b-versatile":
                return httpx.Response(404, json={"error": {
                    "message": "The model `llama-3.3-70b-versatile` does not exist or you do not have access to it.",
                    "code": "model_not_found"}})
            return _chat_ok(body["model"])

        provider = _provider_with_transport(handler)
        res = await provider.generate("hi")
        assert res.content == "ok"
        assert res.model_name == "openai/gpt-oss-120b"
        assert provider._resolved_model == "openai/gpt-oss-120b"

        # Second call goes straight to the healed model: no extra 404 round-trip.
        calls.clear()
        res2 = await provider.generate("hi again")
        assert res2.content == "ok"
        assert calls == [("POST", "/openai/v1/chat/completions")]

    @pytest.mark.asyncio
    async def test_explicit_model_request_is_not_overridden(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            return _chat_ok(body["model"])

        provider = _provider_with_transport(handler)
        provider._resolved_model = "openai/gpt-oss-120b"
        res = await provider.generate("hi", model="qwen/qwen3.6-27b")
        assert res.model_name == "qwen/qwen3.6-27b"

    @pytest.mark.asyncio
    async def test_auth_errors_do_not_trigger_healing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                raise AssertionError("must not consult catalog for auth errors")
            return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

        provider = _provider_with_transport(handler)
        with pytest.raises(LLMProviderError) as err:
            await provider.generate("hi")
        assert "401" in str(err.value)

    @pytest.mark.asyncio
    async def test_empty_catalog_reraises_original_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": []})
            return httpx.Response(404, json={"error": {"message": "model llama-3.3-70b-versatile not found", "code": "model_not_found"}})

        provider = _provider_with_transport(handler)
        with pytest.raises(LLMProviderError):
            await provider.generate("hi")
