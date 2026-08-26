"""Tests for the no-silent-mock rate-limit defenses.

Order of defenses when a request hits a 429:
1. key pool rotation (same provider, another account's quota)
2. sibling model on the same provider (per-model TPM buckets)
3. gateway waits out short cooldowns up to LLM_RATE_LIMIT_MAX_WAIT
4. next provider in the chain
5. offline engine with a visible notice — only when all of the above failed
"""

from __future__ import annotations

import json

import httpx
import pytest

from iris.app.core.config import settings
from iris.app.llm import gateway as gateway_module
from iris.app.llm.base import LLMProvider, LLMProviderError, LLMResponse
from iris.app.llm.cloud import CloudLLMProvider
from iris.app.llm.gateway import ModelGateway


def _ok(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "model": model,
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })


def _per_model_429(model: str) -> httpx.Response:
    return httpx.Response(429, json={"error": {
        "message": f"Rate limit reached for model `{model}` in organization `org_x` "
                   "service tier `on_demand` on tokens per minute (TPM): Limit 8000, "
                   "Used 5650, Requested 2500. Please try again in 12.5s.",
        "type": "tokens", "code": "rate_limit_exceeded"}})


class TestPerModelFallback:
    @pytest.mark.asyncio
    async def test_429_on_default_model_answers_via_sibling_model(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [
                    {"id": "openai/gpt-oss-120b"}, {"id": "openai/gpt-oss-20b"},
                    {"id": "whisper-large-v3"},
                ]})
            body = json.loads(request.content)
            calls.append(body["model"])
            if body["model"] == "openai/gpt-oss-120b":
                return _per_model_429("openai/gpt-oss-120b")
            return _ok(body["model"])

        provider = CloudLLMProvider("groq", "https://api.groq.com/openai/v1", "gsk_x", "openai/gpt-oss-120b")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        res = await provider.generate("hi")
        assert res.content == "ok"
        assert res.model_name == "openai/gpt-oss-20b"
        assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

    @pytest.mark.asyncio
    async def test_sibling_choice_is_not_remembered(self):
        """The preferred model recovers within a minute — next request tries it first."""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "openai/gpt-oss-20b"}]})
            body = json.loads(request.content)
            calls.append(body["model"])
            if body["model"] == "openai/gpt-oss-120b" and len(calls) == 1:
                return _per_model_429("openai/gpt-oss-120b")
            return _ok(body["model"])

        provider = CloudLLMProvider("groq", "https://api.groq.com/openai/v1", "gsk_x", "openai/gpt-oss-120b")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await provider.generate("one")
        await provider.generate("two")
        assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]

    @pytest.mark.asyncio
    async def test_generic_429_does_not_switch_models(self):
        """A key/org-wide limit (no 'for model' scope) must not churn the catalog."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                raise AssertionError("catalog must not be consulted")
            return httpx.Response(429, json={"error": {"message": "Too many requests. Please try again in 30s."}},
                                  headers={"retry-after": "30"})

        provider = CloudLLMProvider("groq", "https://api.groq.com/openai/v1", "gsk_x", "m")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(LLMProviderError) as err:
            await provider.generate("hi")
        assert err.value.retry_after == 30.0


class _ScriptedProvider(LLMProvider):
    """Raises scripted errors, then succeeds."""

    def __init__(self, script):
        super().__init__(provider_name="groq", default_model="m")
        self.script = list(script)
        self.calls = 0

    @property
    def configured(self):
        return True

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        return LLMResponse(content="ok", provider_name="groq", model_name="m")

    async def generate_structured(self, *a, **k): raise NotImplementedError
    async def stream(self, *a, **k): raise NotImplementedError
    async def health_check(self): return True


def _gateway_with(provider, monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODE", "auto")
    monkeypatch.setattr(settings, "LLM_PROVIDER_ORDER", ["groq"])
    gw = ModelGateway.__new__(ModelGateway)
    from iris.app.llm.mock import MockLLMProvider
    from iris.app.llm.gateway import _Circuit
    gw.mock_provider = MockLLMProvider(default_model="mock")
    gw.cloud_providers = {"groq": provider}
    gw._circuits = {"groq": _Circuit()}
    gw._last_good = None
    gw.last_fallback_errors = []
    return gw


class TestGatewayPatience:
    @pytest.mark.asyncio
    async def test_waits_out_repeated_short_limits(self, monkeypatch):
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(gateway_module.asyncio, "sleep", fake_sleep)
        provider = _ScriptedProvider([
            LLMProviderError("groq: HTTP 429", status_code=429, retry_after=12.0),
            LLMProviderError("groq: HTTP 429", status_code=429, retry_after=12.0),
        ])
        gw = _gateway_with(provider, monkeypatch)
        res = await gw.generate("hello")
        assert res.provider_name == "groq"
        assert provider.calls == 3
        assert len(sleeps) == 2

    @pytest.mark.asyncio
    async def test_gives_up_when_budget_exhausted(self, monkeypatch):
        async def fake_sleep(seconds):
            pass

        monkeypatch.setattr(gateway_module.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(settings, "LLM_RATE_LIMIT_MAX_WAIT", 20.0)
        provider = _ScriptedProvider([
            LLMProviderError("groq: HTTP 429", status_code=429, retry_after=15.0),
            LLMProviderError("groq: HTTP 429", status_code=429, retry_after=15.0),  # 30 > 20 budget
        ])
        gw = _gateway_with(provider, monkeypatch)
        res = await gw.generate("hello")
        # Fell back to the offline engine, with the error recorded for the UI notice.
        assert res.provider_name == "mock"
        assert provider.calls == 2
        assert gw.last_fallback_errors
