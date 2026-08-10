"""ModelGateway for capability routing, LLM mode handling, and provider fallbacks."""

from typing import Dict, Any, Optional, Tuple
from nova.app.llm.base import LLMProvider, LLMHealthStatus
from nova.app.llm.mock import MockLLMProvider
from nova.app.llm.local import LocalLLMProvider
from nova.app.llm.remote import RemoteLLMProvider
from nova.app.core.config import settings
from nova.app.core.logging import get_logger

logger = get_logger("model_gateway")


class ModelGateway:
    """Gateway managing LLM providers, capability routing, and fallback strategies."""

    def __init__(self):
        self.providers: Dict[str, LLMProvider] = {}
        
        # 1. Initialize Mock Provider
        self.mock_provider = MockLLMProvider(default_model=settings.DEFAULT_MODEL)
        self.register_provider("mock", self.mock_provider)

        # 2. Initialize Local Provider (AsyncOpenAI compatible client)
        self.local_provider = LocalLLMProvider(
            base_url=settings.LOCAL_LLM_BASE_URL,
            api_key=settings.LOCAL_LLM_API_KEY,
            default_model=settings.LOCAL_LLM_MODEL or settings.DEFAULT_MODEL,
            timeout=settings.LOCAL_LLM_TIMEOUT_SECONDS,
        )
        self.register_provider("local", self.local_provider)

        # 3. Initialize Remote Provider
        self.remote_provider = RemoteLLMProvider(
            base_url=settings.REMOTE_LLM_URL or "https://api.openai.com/v1",
            api_key=settings.REMOTE_LLM_API_KEY,
            default_model=settings.REMOTE_LLM_MODEL or "gpt-4o",
        )
        self.register_provider("remote", self.remote_provider)

        self.mode = settings.LLM_MODE.lower()  # "mock", "local", "auto"

    def register_provider(self, name: str, provider: LLMProvider) -> None:
        self.providers[name.lower()] = provider

    async def get_provider_and_name(self) -> Tuple[LLMProvider, str]:
        """Resolve active LLMProvider and provider name based on LLM_MODE."""
        current_mode = settings.LLM_MODE.lower()

        if current_mode == "mock":
            return self.mock_provider, "mock"

        elif current_mode == "local":
            return self.local_provider, "local"

        elif current_mode == "auto":
            is_local_healthy = await self.local_provider.health_check()
            if is_local_healthy:
                logger.info("Auto mode selected 'local' LLM provider (healthy).")
                return self.local_provider, "local"
            else:
                logger.info("Auto mode falling back to 'mock' LLM provider (local server unreachable).")
                return self.mock_provider, "mock"

        return self.mock_provider, "mock"

    def get_provider(self, provider_name: Optional[str] = None) -> LLMProvider:
        """Synchronous provider getter by explicit name (defaults to mock)."""
        target = (provider_name or "mock").lower()
        return self.providers.get(target, self.mock_provider)

    def select_model(self, capability: str = "DEFAULT") -> str:
        """Route model name by capability tag (FAST, REASONING, VISION, CODING)."""
        model_name, _ = self.select_model_for_capability(capability)
        return model_name or settings.DEFAULT_MODEL

    def select_model_for_capability(self, capability: str = "REASONING") -> Tuple[Optional[str], Optional[str]]:
        """Route model name and error for capability tag (FAST, REASONING, VISION, CODING)."""
        cap = capability.upper()

        if cap == "VISION":
            if not settings.VISION_MODEL:
                return None, "Capability 'VISION' is currently unavailable/not configured."
            return settings.VISION_MODEL, None

        if cap == "FAST":
            return settings.FAST_MODEL, None

        if cap == "CODING":
            return settings.CODING_MODEL, None

        # Default REASONING
        return settings.REASONING_MODEL, None

    async def get_llm_status(self) -> Dict[str, Any]:
        """Get structured LLM status for GET /api/v1/llm/status."""
        current_mode = settings.LLM_MODE.lower()
        provider, provider_name = await self.get_provider_and_name()
        detailed_health = await provider.health_check_detailed()

        capabilities = ["chat", "reasoning", "coding", "fast"]
        if settings.VISION_MODEL:
            capabilities.append("vision")

        model_name = settings.LOCAL_LLM_MODEL or provider.default_model

        return {
            "mode": current_mode,
            "provider": provider_name,
            "model": model_name,
            "available": detailed_health.available,
            "base_url": detailed_health.base_url,
            "latency_ms": detailed_health.latency_ms,
            "capabilities": capabilities,
            "error": detailed_health.error,
        }


default_model_gateway = ModelGateway()
