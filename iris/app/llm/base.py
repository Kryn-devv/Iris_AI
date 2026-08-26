"""Abstract base class for LLM providers in IRIS."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional, Type, TypeVar

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
    latency_ms: Optional[float] = None
    finish_reason: Optional[str] = None


class LLMHealthStatus(BaseModel):
    """Structured health check information for an LLM provider."""

    provider: str
    available: bool
    base_url: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class LLMProviderError(RuntimeError):
    """Raised when a provider call fails; carries retryability information."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        #: Seconds the provider asked us to wait (parsed from a 429), if any.
        self.retry_after = retry_after


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
        """Generate a chat completion."""

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        """Generate a structured completion adhering to a Pydantic schema."""

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream response text chunks."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check availability of the provider."""

    async def health_check_detailed(self) -> LLMHealthStatus:
        """Get detailed health status information."""
        healthy = await self.health_check()
        return LLMHealthStatus(
            provider=self.provider_name,
            available=healthy,
            model=self.default_model,
        )

    async def close(self) -> None:
        """Release any held network resources."""
