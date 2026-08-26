"""Generic OpenAI-compatible chat-completions provider over httpx.

One class serves every free hosted endpoint IRIS supports — OpenRouter, Groq,
Google AI Studio, Cerebras, Mistral, Together, GitHub Models, Hugging Face
router and any custom OpenAI-compatible gateway — because they all speak the
same ``/chat/completions`` protocol. Provider-specific quirks (extra headers,
free-model filtering) are handled by small hooks.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.llm.base import LLMHealthStatus, LLMProvider, LLMProviderError, LLMResponse

logger = get_logger("llm.cloud")

T = TypeVar("T", bound=BaseModel)

#: HTTP status codes worth retrying on another provider.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def _strip_code_fences(text: str) -> str:
    """Remove Markdown code fences wrapping a JSON payload."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def extract_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of the first JSON object embedded in text."""
    candidate = _strip_code_fences(text)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    depth = 0
    start = -1
    for i, ch in enumerate(candidate):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        parsed = json.loads(candidate[start : i + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        continue
    return None


#: Substrings marking models that are not general chat models.
_NON_CHAT_HINTS = (
    "whisper", "tts", "audio", "orpheus", "embed", "moderat", "guard",
    "safety", "safeguard", "rerank", "transcri", "ocr", "-vision-only",
)

#: Chat-model families in rough order of preference for auto-selection.
_CHAT_FAMILY_SCORES = (
    ("gpt-oss", 40), ("deepseek", 32), ("llama", 30), ("qwen", 28),
    ("glm", 28), ("gemini", 26), ("nemotron", 24), ("mistral", 24),
    ("minimax", 22), ("gemma", 20), ("compound", 18), ("claude", 16),
)

_PARAM_SIZE_RE = re.compile(r"(\d{1,3})b\b")


def score_chat_model(model_id: str) -> float:
    """Heuristic quality score for auto-picking a replacement chat model.

    Free-tier catalogs rotate every few months, so any hardcoded default
    eventually 404s. When that happens the provider picks the highest-scoring
    id from its live /models list: known chat families first, bigger parameter
    counts preferred, and everything that is clearly not a general chat model
    (audio, embeddings, safety filters) excluded.
    """
    lowered = model_id.lower()
    if any(hint in lowered for hint in _NON_CHAT_HINTS):
        return -1.0
    score = 10.0
    for family, points in _CHAT_FAMILY_SCORES:
        if family in lowered:
            score += points
            break
    sizes = [int(m) for m in _PARAM_SIZE_RE.findall(lowered)]
    if sizes:
        score += min(max(sizes), 600) / 10.0
    if lowered.endswith(":free"):
        score += 5.0
    return score


class CloudLLMProvider(LLMProvider):
    """Async OpenAI-compatible chat completions client for hosted providers."""

    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: Optional[str],
        default_model: str,
        timeout: Optional[float] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(provider_name=provider_name, default_model=default_model)
        self.base_url = (base_url or "").rstrip("/")
        #: One provider can hold SEVERAL keys (comma-separated in .env).
        #: When a key rate-limits (429) the next one takes over instantly,
        #: so the effective quota is the sum of all keys.
        self.api_keys: List[str] = [
            k for k in (part.strip() for part in (api_key or "").split(",")) if k
        ]
        self._key_index = 0
        #: key -> monotonic time until which it is cooling down.
        self._key_cooldowns: Dict[str, float] = {}
        self.timeout = timeout if timeout is not None else settings.LLM_TIMEOUT_SECONDS
        self.extra_headers = dict(extra_headers or {})
        self._client: Optional[httpx.AsyncClient] = None
        #: Live replacement chosen when the configured model 404s (catalog rotation).
        self._resolved_model: Optional[str] = None

    # ----------------------------------------------------------------- plumbing
    @property
    def configured(self) -> bool:
        """True when this provider has enough configuration to be called."""
        return bool(self.base_url and self.api_keys)

    @property
    def api_key(self) -> Optional[str]:
        """The currently active key (compatibility accessor)."""
        if not self.api_keys:
            return None
        return self.api_keys[self._key_index % len(self.api_keys)]

    def _usable_key_indices(self) -> List[int]:
        """Indices of keys not currently cooling down."""
        now = time.monotonic()
        return [
            i for i, key in enumerate(self.api_keys)
            if self._key_cooldowns.get(key, 0.0) <= now
        ]

    def _cool_current_key(self, seconds: float) -> bool:
        """Bench the active key and switch to the next usable one.

        Returns True when another key is ready to take over right now.
        """
        if not self.api_keys:
            return False
        current = self.api_key
        self._key_cooldowns[current] = time.monotonic() + max(1.0, seconds)
        usable = self._usable_key_indices()
        if not usable:
            return False
        self._key_index = usable[0]
        logger.info(
            "%s: switching to API key %d/%d (previous key cooling for %.0fs).",
            self.provider_name, self._key_index + 1, len(self.api_keys), seconds,
        )
        return True

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        headers.update(self.extra_headers)
        return headers

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _redact(self, text: str) -> str:
        for key in self.api_keys:
            if key in text:
                text = text.replace(key, "[REDACTED]")
        return text

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str],
        history: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if history:
            messages.extend(history)
        if system_prompt and not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": system_prompt})
        if prompt:
            messages.append({"role": "user", "content": prompt})
        return messages

    async def _post_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /chat/completions, walking the key pool on per-key failures.

        A 429 benches the limited key for exactly the provider-requested
        time and the next key answers the same request immediately; a
        401/403 benches a bad key for long. Errors surface only when every
        key in the pool is unusable.
        """
        client = self._client_or_create()
        url = f"{self.base_url}/chat/completions"

        attempts = max(1, len(self.api_keys))
        last_error: Optional[LLMProviderError] = None
        for _ in range(attempts):
            try:
                response = await client.post(url, json=payload, headers=self._headers())
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                raise LLMProviderError(
                    f"{self.provider_name}: connection failed ({type(exc).__name__})", retryable=True
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMProviderError(
                    f"{self.provider_name}: transport error ({self._redact(str(exc))})", retryable=True
                ) from exc

            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError as exc:
                    raise LLMProviderError(
                        f"{self.provider_name}: invalid JSON response", retryable=True
                    ) from exc

            body_preview = self._redact(response.text[:400])
            retryable = response.status_code in _RETRYABLE_STATUS
            retry_after = None
            if response.status_code == 429:
                header = response.headers.get("retry-after")
                if header and header.replace(".", "", 1).isdigit():
                    retry_after = float(header)
                else:
                    hinted = re.search(r"try again in ([0-9.]+)s", response.text)
                    if hinted:
                        retry_after = float(hinted.group(1))
            last_error = LLMProviderError(
                f"{self.provider_name}: HTTP {response.status_code} — {body_preview}",
                retryable=retryable,
                status_code=response.status_code,
                retry_after=retry_after,
            )

            # Per-KEY failures: hand the same request to the next key.
            if response.status_code == 429 and len(self.api_keys) > 1:
                if self._cool_current_key(retry_after or 60.0):
                    continue
            elif response.status_code in (401, 403) and len(self.api_keys) > 1:
                if self._cool_current_key(900.0):
                    continue
            break

        assert last_error is not None
        raise last_error

    async def list_model_ids(self) -> List[str]:
        """Ids from the provider's live /models endpoint ([] on any failure)."""
        client = self._client_or_create()
        try:
            response = await client.get(f"{self.base_url}/models", headers=self._headers())
            if response.status_code != 200:
                return []
            data = response.json()
            items = data.get("data") if isinstance(data, dict) else data
            if not isinstance(items, list):
                return []
            return [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError):
            return []

    async def _resolve_fallback_model(self, exclude: Optional[str] = None) -> Optional[str]:
        """Best-scoring chat model from the live catalog, or None."""
        best_id, best_score = None, 0.0
        for model_id in await self.list_model_ids():
            if model_id == exclude:
                continue
            score = score_chat_model(model_id)
            if score > best_score:
                best_id, best_score = model_id, score
        return best_id

    @staticmethod
    def _looks_like_missing_model(exc: "LLMProviderError") -> bool:
        """True when the provider rejected the request because the model is gone."""
        if getattr(exc, "status_code", None) not in (400, 404):
            return False
        message = str(exc).lower()
        if "model" not in message:
            return False
        return any(
            hint in message
            for hint in (
                "not exist", "not found", "model_not_found", "decommission",
                "deprecat", "no longer", "invalid model", "unknown model",
            )
        )

    def _target_model(self, requested: Optional[str]) -> str:
        """Effective model: an explicit non-default request wins; otherwise the
        healed replacement (if any) shadows a stale configured default."""
        wanted = requested or self.default_model
        if self._resolved_model and wanted == self.default_model:
            return self._resolved_model
        return wanted

    # --------------------------------------------------------------- interface
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
        if not self.configured:
            raise LLMProviderError(f"{self.provider_name}: not configured (missing API key).", retryable=False)

        target_model = self._target_model(model)
        messages = self._build_messages(prompt, system_prompt, kwargs.get("messages"))

        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        payload["max_tokens"] = max_tokens or settings.LLM_MAX_TOKENS
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs["response_format"]

        start = time.perf_counter()
        try:
            data = await self._post_chat(payload)
        except LLMProviderError as exc:
            # Self-heal a rotated catalog: the configured model no longer
            # exists, so pick the best live replacement and retry once.
            if not self._looks_like_missing_model(exc):
                raise
            fallback = await self._resolve_fallback_model(exclude=target_model)
            if not fallback:
                raise
            logger.warning(
                "%s: model '%s' is gone from the catalog; auto-switching to '%s'.",
                self.provider_name, target_model, fallback,
            )
            self._resolved_model = fallback
            target_model = fallback
            payload["model"] = fallback
            data = await self._post_chat(payload)
        latency = (time.perf_counter() - start) * 1000.0

        choices = data.get("choices") or []
        if not choices:
            raise LLMProviderError(f"{self.provider_name}: response had no choices.", retryable=True)

        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        usage = data.get("usage") or {}

        tool_calls = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                fn = tc.get("function") or {}
                tool_calls.append(
                    {
                        "id": tc.get("id"),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", "{}"),
                        },
                    }
                )

        logger.info(
            "%s completion ok (model=%s, %.0fms, %s tool calls)",
            self.provider_name, target_model, latency, len(tool_calls or []),
        )

        return LLMResponse(
            content=content,
            provider_name=self.provider_name,
            model_name=data.get("model") or target_model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw_response=data,
            tool_calls=tool_calls,
            latency_ms=round(latency, 1),
            finish_reason=choices[0].get("finish_reason"),
        )

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        schema_json = json.dumps(response_model.model_json_schema())
        augmented_system = (
            (system_prompt or "")
            + "\nCRITICAL: Respond ONLY with a single valid JSON object matching this schema"
            + " — no prose, no code fences:\n"
            + schema_json
        ).strip()

        last_error: Optional[Exception] = None
        for attempt in range(2):
            res = await self.generate(
                prompt=prompt,
                system_prompt=augmented_system,
                model=model,
                temperature=0.2 if attempt == 0 else 0.0,
                **kwargs,
            )
            parsed = extract_json_object(res.content)
            if parsed is not None:
                try:
                    return response_model.model_validate(parsed)
                except ValidationError as exc:
                    last_error = exc
            augmented_system += "\nYour previous answer was not valid JSON for the schema. Try again."

        raise LLMProviderError(
            f"{self.provider_name}: could not produce valid structured output ({last_error}).",
            retryable=True,
        )

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        if not self.configured:
            raise LLMProviderError(f"{self.provider_name}: not configured (missing API key).", retryable=False)

        target_model = self._target_model(model)
        messages = self._build_messages(prompt, system_prompt, kwargs.get("messages"))
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": kwargs.get("temperature", settings.LLM_TEMPERATURE),
            "max_tokens": kwargs.get("max_tokens") or settings.LLM_MAX_TOKENS,
            "stream": True,
        }

        client = self._client_or_create()
        url = f"{self.base_url}/chat/completions"
        try:
            async with client.stream("POST", url, json=payload, headers=self._headers()) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise LLMProviderError(
                        f"{self.provider_name}: HTTP {response.status_code} — {self._redact(body.decode(errors='replace')[:300])}",
                        retryable=response.status_code in _RETRYABLE_STATUS,
                        status_code=response.status_code,
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    delta = ((event.get("choices") or [{}])[0].get("delta") or {})
                    piece = delta.get("content")
                    if piece:
                        yield piece
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            raise LLMProviderError(
                f"{self.provider_name}: stream failed ({type(exc).__name__})", retryable=True
            ) from exc

    async def health_check(self) -> bool:
        if not self.configured:
            return False
        client = self._client_or_create()
        try:
            response = await client.get(f"{self.base_url}/models", headers=self._headers())
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def health_check_detailed(self) -> LLMHealthStatus:
        if not self.configured:
            return LLMHealthStatus(
                provider=self.provider_name,
                available=False,
                base_url=self.base_url or None,
                model=self.default_model,
                error="Not configured (missing API key).",
            )
        start = time.perf_counter()
        healthy = await self.health_check()
        latency = (time.perf_counter() - start) * 1000.0
        return LLMHealthStatus(
            provider=self.provider_name,
            available=healthy,
            base_url=self.base_url,
            model=self.default_model,
            latency_ms=round(latency, 1),
            error=None if healthy else "Provider unreachable or key rejected.",
        )


def clean_credential(value: Optional[str]) -> Optional[str]:
    """Normalize a pasted credential: trim whitespace/newlines and wrapping quotes.

    Notepad and PowerShell pastes routinely smuggle a trailing newline or the
    surrounding quotes into ``.env`` values; every provider then 401s while the
    key itself is perfectly valid.
    """
    if value is None:
        return None
    cleaned = value.strip()
    while len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned or None


def build_provider(name: str, credentials: Dict[str, Optional[str]]) -> CloudLLMProvider:
    """Construct a provider from a name and its settings credentials."""
    extra_headers: Dict[str, str] = {}
    if name == "openrouter":
        # OpenRouter asks apps to identify themselves; also enables app leaderboards.
        extra_headers = {
            "HTTP-Referer": settings.OPENROUTER_APP_URL,
            "X-Title": settings.OPENROUTER_APP_TITLE,
        }
    raw_key = credentials.get("api_key") or ""
    cleaned_keys = ",".join(
        k for k in (clean_credential(part) for part in raw_key.split(",")) if k
    )
    return CloudLLMProvider(
        provider_name=name,
        base_url=(clean_credential(credentials.get("base_url")) or ""),
        api_key=cleaned_keys or None,
        default_model=clean_credential(credentials.get("model")) or "auto",
        extra_headers=extra_headers,
    )
