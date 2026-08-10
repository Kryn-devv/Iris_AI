"""Remote Cloud LLM Provider Adapter (OpenAI-compatible endpoints)."""

import json
from typing import Dict, Any, Optional, AsyncGenerator, Type, TypeVar
import httpx
from pydantic import BaseModel

from nova.app.llm.base import LLMProvider, LLMResponse
from nova.app.core.logging import get_logger

logger = get_logger("remote_llm")

T = TypeVar("T", bound=BaseModel)


class RemoteLLMProvider(LLMProvider):
    """Adapter for OpenAI-compatible remote LLM APIs."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: Optional[str] = None,
        default_model: str = "gpt-4o",
        timeout: float = 60.0,
    ):
        super().__init__(provider_name="remote", default_model=default_model)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{self.base_url}/models", headers=headers)
                return res.status_code == 200
        except Exception:
            return False

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if not self.api_key:
            raise ValueError("Remote LLM provider requires an API key.")

        target_model = model or self.default_model
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                res.raise_for_status()
                data = res.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return LLMResponse(
                    content=content,
                    provider_name=self.provider_name,
                    model_name=target_model,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"Error calling remote LLM API: {e}")
            raise RuntimeError(f"Remote LLM request failed: {e}") from e

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        schema_json = json.dumps(response_model.model_json_schema())
        augmented_system = (system_prompt or "") + f"\nOutput ONLY valid JSON adhering to schema: {schema_json}"
        res = await self.generate(prompt, system_prompt=augmented_system, model=model, **kwargs)
        try:
            parsed = json.loads(res.content)
            return response_model.model_validate(parsed)
        except Exception as e:
            raise ValueError(f"Failed to parse JSON response from remote LLM: {res.content}") from e

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        # Minimal mock chunking for stream interface
        res = await self.generate(prompt, system_prompt, model, **kwargs)
        for chunk in res.content.split(" "):
            yield chunk + " "
