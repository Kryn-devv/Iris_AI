"""ModelGateway: multi-provider routing with fallback and circuit breaking.

IRIS treats hosted models as *optional accelerators*, never as a hard
dependency. The gateway walks the configured provider chain (OpenRouter →
Groq → Gemini → ...) in the user's preferred order; a provider that errors is
put on a cooldown ("circuit break") so subsequent requests skip it; and when no
provider is configured or reachable, the deterministic offline
:class:`MockLLMProvider` answers so the assistant keeps working.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.llm.base import LLMProvider, LLMProviderError, LLMResponse
from iris.app.llm.cloud import CloudLLMProvider, build_provider
from iris.app.llm.mock import MockLLMProvider

logger = get_logger("llm.gateway")


class _Circuit:
    """Cooldown bookkeeping for one provider."""

    __slots__ = ("failures", "open_until", "last_error")

    def __init__(self) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.last_error: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self.open_until

    def record_failure(self, error: str, cooldown: float) -> None:
        self.failures += 1
        self.last_error = error
        # Exponential-ish backoff capped at 10 minutes.
        self.open_until = time.monotonic() + min(cooldown * self.failures, 600.0)

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.last_error = None


class ModelGateway:
    """Gateway managing LLM providers, fallback routing and capability models."""

    def __init__(self) -> None:
        self.mock_provider = MockLLMProvider(default_model=settings.DEFAULT_MODEL)
        self.cloud_providers: Dict[str, CloudLLMProvider] = {}
        #: Why the most recent request fell back to the offline engine
        #: (cleared on the next cloud success). Surfaced in chat replies.
        self.last_fallback_errors: List[str] = []
        self._circuits: Dict[str, _Circuit] = {}
        self._last_good: Optional[str] = None
        self.rebuild()

    # ------------------------------------------------------------------ build
    def rebuild(self) -> None:
        """(Re)construct cloud providers from current settings."""
        creds = settings.provider_credentials()
        self.cloud_providers = {}
        for name in settings.LLM_PROVIDER_ORDER:
            info = creds.get(name)
            if not info:
                continue
            provider = build_provider(name, info)
            self.cloud_providers[name] = provider
            self._circuits.setdefault(name, _Circuit())

    # ------------------------------------------------------------------ chain
    def provider_chain(self) -> List[CloudLLMProvider]:
        """Configured providers in preference order, healthiest first."""
        chain: List[CloudLLMProvider] = []
        preferred: List[CloudLLMProvider] = []
        for name in settings.LLM_PROVIDER_ORDER:
            provider = self.cloud_providers.get(name)
            if provider is None or not provider.configured:
                continue
            if name == self._last_good:
                preferred.append(provider)
            else:
                chain.append(provider)
        return preferred + chain

    def _usable_chain(self) -> List[CloudLLMProvider]:
        """Provider chain with open circuits filtered out."""
        return [p for p in self.provider_chain() if not self._circuits[p.provider_name].is_open]

    @property
    def has_cloud(self) -> bool:
        return bool(self.provider_chain())

    # -------------------------------------------------------------- selection
    async def get_provider_and_name(self) -> Tuple[LLMProvider, str]:
        """Resolve the active provider based on LLM_MODE (compatibility API)."""
        mode = settings.LLM_MODE
        if mode in ("off", "mock"):
            return self.mock_provider, "mock"
        chain = self._usable_chain() or self.provider_chain()
        if chain:
            provider = chain[0]
            return provider, provider.provider_name
        return self.mock_provider, "mock"

    def get_provider(self, provider_name: Optional[str] = None) -> LLMProvider:
        """Synchronous provider getter by explicit name (defaults to best)."""
        if provider_name:
            target = provider_name.lower()
            if target == "mock":
                return self.mock_provider
            if target in self.cloud_providers:
                return self.cloud_providers[target]
        chain = self.provider_chain()
        return chain[0] if chain else self.mock_provider

    def select_model(self, capability: str = "DEFAULT") -> str:
        """Model name for a capability tag (FAST, REASONING, VISION, CODING)."""
        override = settings.capability_model(capability)
        if override:
            return override
        chain = self.provider_chain()
        if chain:
            return chain[0].default_model
        return settings.DEFAULT_MODEL

    def select_model_for_capability(self, capability: str = "REASONING") -> Tuple[Optional[str], Optional[str]]:
        """(model, error) for a capability tag."""
        cap = capability.upper()
        override = settings.capability_model(cap)
        if override:
            return override, None
        if cap == "VISION" and not settings.VISION_MODEL:
            chain = self.provider_chain()
            if not chain:
                return None, "Capability 'VISION' is unavailable: no vision model configured."
        return self.select_model(cap), None

    # ------------------------------------------------------------- generation
    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        capability: str = "REASONING",
        tools: Optional[list] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate with automatic fallback through the provider chain.

        Raises :class:`LLMProviderError` only when *every* option including the
        offline mock is exhausted (which the mock never is).
        """
        mode = settings.LLM_MODE
        temp = settings.LLM_TEMPERATURE if temperature is None else temperature

        if mode in ("off", "mock"):
            return await self.mock_provider.generate(
                prompt, system_prompt=system_prompt, tools=tools, temperature=temp, **kwargs
            )

        errors: List[str] = []
        for provider in self._usable_chain():
            circuit = self._circuits[provider.provider_name]
            try:
                # Rate limits are pauses, not outages: keep honouring the
                # provider's own "try again in Ns" until the patience budget
                # (LLM_RATE_LIMIT_MAX_WAIT) is spent, then move on.
                waited = 0.0
                while True:
                    try:
                        response = await provider.generate(
                            prompt,
                            system_prompt=system_prompt,
                            model=model or settings.capability_model(capability) or provider.default_model,
                            temperature=temp,
                            max_tokens=max_tokens,
                            tools=tools,
                            **kwargs,
                        )
                        break
                    except LLMProviderError as exc:
                        wait = getattr(exc, "retry_after", None)
                        if (
                            exc.status_code != 429
                            or wait is None
                            or waited + wait > settings.LLM_RATE_LIMIT_MAX_WAIT
                        ):
                            raise
                        waited += wait
                        logger.info(
                            "Provider '%s' rate-limited; waiting %.1fs as requested (%.1fs of %.0fs budget).",
                            provider.provider_name, wait, waited, settings.LLM_RATE_LIMIT_MAX_WAIT,
                        )
                        await asyncio.sleep(wait + 0.5)
                circuit.record_success()
                self.last_fallback_errors = []
                if self._last_good != provider.provider_name:
                    self._last_good = provider.provider_name
                    default_event_bus.publish(
                        Topics.LLM_ROUTE,
                        {"provider": provider.provider_name, "model": response.model_name},
                    )
                return response
            except LLMProviderError as exc:
                errors.append(str(exc))
                logger.warning("Provider '%s' failed: %s", provider.provider_name, exc)
                wait = getattr(exc, "retry_after", None)
                if exc.status_code == 429:
                    # Cool down for exactly as long as the provider asked —
                    # a rate limit is a pause, not an outage.
                    circuit.open_until = time.monotonic() + min(wait or 30.0, 120.0)
                    circuit.last_error = str(exc)
                elif exc.retryable:
                    circuit.record_failure(str(exc), settings.LLM_CIRCUIT_BREAK_SECONDS)
                else:
                    # Bad key / misconfiguration: long cooldown to avoid spam.
                    circuit.record_failure(str(exc), 900.0)
                continue

        if errors:
            self.last_fallback_errors = errors[-3:]
            default_event_bus.publish(Topics.LLM_FALLBACK, {"errors": errors[-3:]})
            logger.info("All cloud providers failed; falling back to offline engine.")
        return await self.mock_provider.generate(
            prompt, system_prompt=system_prompt, tools=tools, temperature=temp, **kwargs
        )

    async def generate_structured(self, prompt: str, response_model: type, **kwargs: Any) -> Any:
        """Structured generation with the same fallback semantics."""
        mode = settings.LLM_MODE
        if mode not in ("off", "mock"):
            for provider in self._usable_chain():
                circuit = self._circuits[provider.provider_name]
                try:
                    result = await provider.generate_structured(prompt, response_model, **kwargs)
                    circuit.record_success()
                    self._last_good = provider.provider_name
                    return result
                except LLMProviderError as exc:
                    logger.warning("Structured call to '%s' failed: %s", provider.provider_name, exc)
                    circuit.record_failure(str(exc), settings.LLM_CIRCUIT_BREAK_SECONDS)
                    continue
        return await self.mock_provider.generate_structured(prompt, response_model, **kwargs)

    # ----------------------------------------------------------------- status
    async def get_llm_status(self) -> Dict[str, Any]:
        """Structured LLM status for GET /api/v1/llm/status."""
        mode = settings.LLM_MODE
        provider, provider_name = await self.get_provider_and_name()

        providers_report = []
        for name in settings.LLM_PROVIDER_ORDER:
            cloud = self.cloud_providers.get(name)
            if cloud is None:
                continue
            circuit = self._circuits.get(name)
            providers_report.append(
                {
                    "name": name,
                    "configured": cloud.configured,
                    "keys": len(cloud.api_keys),
                    "model": cloud.default_model,
                    "base_url": cloud.base_url,
                    "circuit_open": bool(circuit and circuit.is_open),
                    "consecutive_failures": circuit.failures if circuit else 0,
                    "last_error": circuit.last_error if circuit else None,
                    "active": name == provider_name,
                }
            )

        capabilities = ["chat", "reasoning", "coding", "fast", "tools"]
        if settings.VISION_MODEL:
            capabilities.append("vision")

        detailed = await provider.health_check_detailed()
        return {
            "mode": mode,
            "provider": provider_name,
            "model": detailed.model or provider.default_model,
            "available": detailed.available,
            "base_url": detailed.base_url,
            "latency_ms": detailed.latency_ms,
            "capabilities": capabilities,
            "error": detailed.error,
            "offline_fallback": "mock",
            "providers": providers_report,
        }

    async def close(self) -> None:
        for provider in self.cloud_providers.values():
            await provider.close()


default_model_gateway = ModelGateway()
