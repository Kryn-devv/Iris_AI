"""Tests for multi-key pools: rotate on rate limits, bench bad keys.

GROQ_API_KEY=key1,key2,key3 gives one provider several accounts' quotas:
when a key 429s, the next key answers the SAME request immediately, so the
user only sees an error when every key in the pool is exhausted.
"""

from __future__ import annotations

import json

import httpx
import pytest

from iris.app.llm.base import LLMProviderError
from iris.app.llm.cloud import CloudLLMProvider, build_provider


def _provider(keys: str, handler) -> CloudLLMProvider:
    provider = CloudLLMProvider(
        provider_name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key=keys,
        default_model="openai/gpt-oss-120b",
    )
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def _ok(model: str = "openai/gpt-oss-120b") -> httpx.Response:
    return httpx.Response(200, json={
        "model": model,
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })


def _limited(seconds: float = 12.0) -> httpx.Response:
    return httpx.Response(429, json={
        "error": {"message": f"Rate limit reached. Please try again in {seconds}s.",
                  "code": "rate_limit_exceeded"}})


class TestKeyPoolParsing:
    def test_single_key_unchanged(self):
        p = CloudLLMProvider("groq", "https://x", "gsk_only", "m")
        assert p.api_keys == ["gsk_only"]
        assert p.api_key == "gsk_only"
        assert p.configured

    def test_comma_separated_pool(self):
        p = CloudLLMProvider("groq", "https://x", "gsk_a, gsk_b ,gsk_c", "m")
        assert p.api_keys == ["gsk_a", "gsk_b", "gsk_c"]
        assert p.api_key == "gsk_a"

    def test_build_provider_cleans_each_key(self):
        p = build_provider("groq", {
            "api_key": ' "gsk_a" , gsk_b\n', "base_url": "https://api.groq.com/openai/v1", "model": "m",
        })
        assert p.api_keys == ["gsk_a", "gsk_b"]

    def test_empty_parts_dropped(self):
        p = CloudLLMProvider("groq", "https://x", "gsk_a,,  ,gsk_b", "m")
        assert p.api_keys == ["gsk_a", "gsk_b"]


class TestKeyRotation:
    @pytest.mark.asyncio
    async def test_429_switches_key_and_same_request_succeeds(self):
        seen_keys = []

        def handler(request: httpx.Request) -> httpx.Response:
            key = request.headers["authorization"].removeprefix("Bearer ")
            seen_keys.append(key)
            return _limited() if key == "gsk_a" else _ok()

        provider = _provider("gsk_a,gsk_b", handler)
        res = await provider.generate("hello")
        assert res.content == "ok"
        assert seen_keys == ["gsk_a", "gsk_b"]

    @pytest.mark.asyncio
    async def test_next_request_starts_on_fresh_key(self):
        seen_keys = []

        def handler(request: httpx.Request) -> httpx.Response:
            key = request.headers["authorization"].removeprefix("Bearer ")
            seen_keys.append(key)
            return _limited() if key == "gsk_a" else _ok()

        provider = _provider("gsk_a,gsk_b", handler)
        await provider.generate("one")
        await provider.generate("two")
        # gsk_a is cooling: request two must go straight to gsk_b.
        assert seen_keys == ["gsk_a", "gsk_b", "gsk_b"]

    @pytest.mark.asyncio
    async def test_all_keys_limited_raises_with_retry_after(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _limited(7.0)

        provider = _provider("gsk_a,gsk_b,gsk_c", handler)
        with pytest.raises(LLMProviderError) as err:
            await provider.generate("hello")
        assert err.value.status_code == 429
        assert err.value.retry_after == 7.0

    @pytest.mark.asyncio
    async def test_invalid_key_benched_pool_survives(self):
        seen_keys = []

        def handler(request: httpx.Request) -> httpx.Response:
            key = request.headers["authorization"].removeprefix("Bearer ")
            seen_keys.append(key)
            if key == "gsk_revoked":
                return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
            return _ok()

        provider = _provider("gsk_revoked,gsk_good", handler)
        res = await provider.generate("hello")
        assert res.content == "ok"
        # The bad key is benched long: next call skips it entirely.
        await provider.generate("again")
        assert seen_keys == ["gsk_revoked", "gsk_good", "gsk_good"]

    @pytest.mark.asyncio
    async def test_single_key_429_still_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _limited(3.0)

        provider = _provider("gsk_only", handler)
        with pytest.raises(LLMProviderError) as err:
            await provider.generate("hello")
        assert err.value.retry_after == 3.0

    def test_redact_hides_every_key(self):
        provider = CloudLLMProvider("groq", "https://x", "gsk_a,gsk_b", "m")
        text = provider._redact("first gsk_a then gsk_b done")
        assert "gsk_a" not in text and "gsk_b" not in text
        assert text.count("[REDACTED]") == 2
