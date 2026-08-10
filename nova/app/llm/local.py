"""Local LLM Provider Adapter using OpenAI-compatible AsyncOpenAI client."""

import time
import json
from typing import Dict, Any, Optional, AsyncGenerator, Type, TypeVar
import openai
from openai import AsyncOpenAI, APIError, APIConnectionError, APITimeoutError
from pydantic import BaseModel

from nova.app.llm.base import LLMProvider, LLMResponse, LLMHealthStatus
from nova.app.agent.prompts import get_system_prompt
from nova.app.core.logging import get_logger

logger = get_logger("local_llm")

T = TypeVar("T", bound=BaseModel)


class LocalLLMProvider(LLMProvider):
    """Adapter for self-hosted OpenAI-compatible LLM servers (e.g. vLLM, Ollama, LocalAI)."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        default_model: Optional[str] = None,
        timeout: float = 60.0,
    ):
        target_model = default_model or "local-model"
        super().__init__(provider_name="local", default_model=target_model)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "EMPTY"
        self.timeout = timeout
        self.client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    async def health_check(self) -> bool:
        """Check if the local inference server is reachable."""
        try:
            await self.client.models.list()
            return True
        except Exception as e:
            logger.debug(f"Local LLM health check ping failed: {e}")
            return False

    async def health_check_detailed(self) -> LLMHealthStatus:
        """Get structured health status with latency measurements without leaking credentials."""
        start_time = time.perf_counter()
        try:
            models_page = await self.client.models.list()
            latency = (time.perf_counter() - start_time) * 1000.0
            
            model_name = self.default_model
            if hasattr(models_page, "data") and isinstance(models_page.data, list) and models_page.data:
                model_name = getattr(models_page.data[0], "id", self.default_model)

            return LLMHealthStatus(
                provider=self.provider_name,
                available=True,
                base_url=self.base_url,
                model=model_name,
                latency_ms=round(latency, 2),
                error=None,
            )
        except Exception as e:
            latency = (time.perf_counter() - start_time) * 1000.0
            err_str = str(e)
            if self.api_key and self.api_key != "EMPTY":
                err_str = err_str.replace(self.api_key, "[REDACTED]")
            logger.warning(f"Local LLM detailed health check error: {err_str}")
            return LLMHealthStatus(
                provider=self.provider_name,
                available=False,
                base_url=self.base_url,
                model=self.default_model,
                latency_ms=round(latency, 2),
                error=err_str,
            )

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
        """Generate text completion via OpenAI-compatible chat.completions API."""
        target_model = model or self.default_model
        sys_prompt = system_prompt or get_system_prompt()

        messages = [
            {"role": "system", "content": sys_prompt},
        ]
        if "messages" in kwargs and isinstance(kwargs["messages"], list):
            messages.extend(kwargs["messages"])
        if prompt:
            messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        start_time = time.perf_counter()
        logger.info(f"Sending chat completion request to local LLM provider (model='{target_model}')")

        try:
            response = await self.client.chat.completions.create(**payload)
            latency = (time.perf_counter() - start_time) * 1000.0
            logger.info(f"Local LLM completion succeeded in {latency:.2f}ms")

            choice = response.choices[0]
            content = choice.message.content or ""
            usage = getattr(response, "usage", None)

            prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            completion_tokens = getattr(usage, "completion_tokens", None) if usage else None

            raw_tool_calls = getattr(choice.message, "tool_calls", None)
            extracted_tool_calls = None
            if raw_tool_calls:
                extracted_tool_calls = []
                for tc in raw_tool_calls:
                    fn_obj = getattr(tc, "function", None)
                    fn_name = getattr(fn_obj, "name", "") if fn_obj else ""
                    fn_args = getattr(fn_obj, "arguments", "{}") if fn_obj else "{}"
                    extracted_tool_calls.append({
                        "id": getattr(tc, "id", None),
                        "type": getattr(tc, "type", "function"),
                        "function": {
                            "name": fn_name,
                            "arguments": fn_args,
                        }
                    })

            raw_dict = None
            if isinstance(response, BaseModel):
                raw_dict = response.model_dump()
            elif isinstance(response, dict):
                raw_dict = response

            return LLMResponse(
                content=content,
                provider_name=self.provider_name,
                model_name=target_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                raw_response=raw_dict,
                tool_calls=extracted_tool_calls,
            )

        except (APIConnectionError, APITimeoutError) as e:
            logger.error(f"Local LLM connection/timeout error: {e}")
            raise RuntimeError(f"Local LLM server unavailable: {e}") from e
        except APIError as e:
            logger.error(f"Local LLM API error: {e}")
            raise RuntimeError(f"Local LLM execution failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during Local LLM completion: {e}")
            raise RuntimeError(f"Local LLM failed: {e}") from e

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        """Generate structured completion adhering to Pydantic schema."""
        schema_json = json.dumps(response_model.model_json_schema())
        augmented_system = (
            (system_prompt or get_system_prompt())
            + f"\nCRITICAL REQUIREMENT: Respond ONLY with a valid JSON object matching this schema:\n{schema_json}"
        )

        try:
            res = await self.generate(
                prompt=prompt,
                system_prompt=augmented_system,
                model=model,
                temperature=0.1,
                **kwargs,
            )
            content = res.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            parsed = json.loads(content)
            return response_model.model_validate(parsed)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from local LLM response: {e}")
            raise ValueError(f"Local LLM structured output invalid JSON: {e}") from e
        except Exception as e:
            logger.error(f"Local LLM structured output error: {e}")
            raise RuntimeError(f"Local LLM structured output capability error: {e}") from e

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream response tokens chunk by chunk."""
        target_model = model or self.default_model
        sys_prompt = system_prompt or get_system_prompt()

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            stream_response = await self.client.chat.completions.create(
                model=target_model,
                messages=messages,
                stream=True,
                **kwargs,
            )
            async for chunk in stream_response:
                if hasattr(chunk, "choices") and chunk.choices:
                    delta = getattr(chunk.choices[0], "delta", None)
                    token = getattr(delta, "content", None) if delta else None
                    if token:
                        yield token
        except Exception as e:
            logger.error(f"Local LLM streaming error: {e}")
            raise RuntimeError(f"Streaming failed: {e}") from e
