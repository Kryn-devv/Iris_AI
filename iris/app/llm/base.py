"""Abstract Base Class for LLM Providers in IRIS."""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncGenerator, Type, TypeVar, List
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMResponse(BaseModel):
    """Standardized response structure from an LLM call."""
    content: str
    raw_response: Optional[Dict[str, Any]] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    provider_name: str
    model_name: str
    tool_calls: Optional[List[Dict[str, Any]]] = None


class LLMHealthStatus(BaseModel):
    """Structured health check information for an LLM Provider."""
    provider: str
    available: bool
    base_url: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class LLMProvider(ABC):
    """Abstract interface that all LLM backends must implement."""

    def __init__(self, provider_name: str, default_model: str):
        self.provider_name = provider_name
        self.default_model = default_model

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[list] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate text completion."""
        pass

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        """Generate structured completion adhering to a Pydantic schema."""
        pass

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream response chunks."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check availability of the provider."""
        pass

    async def health_check_detailed(self) -> LLMHealthStatus:
        """Get detailed health status information."""
        healthy = await self.health_check()
        return LLMHealthStatus(
            provider=self.provider_name,
            available=healthy,
            model=self.default_model,
        )
