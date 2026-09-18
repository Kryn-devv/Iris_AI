"""First-run setup: paste a key, press enter, start talking.

Editing ``.env`` in a text editor and restarting is a fine way to configure a
server and a poor way to start using an assistant — especially the second and
third time, when the only thing changing is one key. This is the same
configuration, done from the screen that is already open.

Two things make it more than a text box:

**The key is verified before it is saved.** A real (tiny) completion goes to
the provider first, so a typo, a revoked key or a wrong provider is reported in
seconds, in words, instead of becoming a silent fallback to the offline engine
that the user discovers an hour later.

**A failed write is reported as a failed write.** If ``.env`` cannot be
written, the key still applies to the running process — but the response says
so plainly rather than claiming it was saved and losing it at the next restart.

Secrets go one way. Keys are never returned, never logged, and read back only
masked.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from iris.app.api.dependencies import get_model_gateway
from iris.app.core import envfile
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.llm.base import LLMProviderError
from iris.app.llm.cloud import build_provider
from iris.app.llm.gateway import ModelGateway

logger = get_logger("api.setup")

router = APIRouter(prefix="/api/v1/setup", tags=["Setup"])

#: The free-tier providers worth offering on a first run, best first. Everything
#: else stays available through ``.env``; a setup screen listing nine providers
#: is a decision, not a welcome.
PROVIDERS: List[Dict[str, str]] = [
    {
        "name": "gemini",
        "label": "Google Gemini",
        "key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-flash-latest",
        "signup": "https://aistudio.google.com/apikey",
        "hint": "Usually starts with AIza",
        "note": "Free tier, and the same model can read pictures for the camera.",
    },
    {
        "name": "openrouter",
        "label": "OpenRouter",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "default_model": "z-ai/glm-5.2:free",
        "signup": "https://openrouter.ai/keys",
        "hint": "Usually starts with sk-or-",
        "note": "One key, many free models.",
    },
    {
        "name": "groq",
        "label": "Groq",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "signup": "https://console.groq.com/keys",
        "hint": "Usually starts with gsk_",
        "note": "Very fast, with a tight free rate limit.",
    },
]

_BY_NAME = {p["name"]: p for p in PROVIDERS}

#: Providers whose model also accepts images, so the camera works with the same
#: key instead of needing a second setting nobody knows to look for.
_VISION_CAPABLE = {"gemini"}

#: Models to try when the default one is gone. Providers rename and retire
#: models without warning, and when that happens the failure lands on someone
#: pasting their first key — who is told their brand-new key does not work, and
#: has no way to know the only wrong thing was a name baked into this file
#: months ago. Each attempt is one tiny request, so trying a few costs a second
#: and saves the whole first run. Only used when the caller did not name a
#: model: an explicit choice is never quietly replaced.
_FALLBACK_MODELS: Dict[str, List[str]] = {
    "gemini": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"],
    "openrouter": ["deepseek/deepseek-chat-v3.1:free", "meta-llama/llama-3.3-70b-instruct:free"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
}


class SetupRequest(BaseModel):
    """Any subset: a key, a name, or both."""

    provider: Optional[str] = Field(default=None, description="gemini | openrouter | groq")
    api_key: Optional[str] = Field(default=None, description="Pasted key. Never stored in the response.")
    model: Optional[str] = Field(default=None, description="Override the provider's default model.")
    user_name: Optional[str] = Field(default=None, description="What she should call you.")
    #: Also point the camera's vision model at this provider when it can see.
    use_for_vision: bool = True
    #: Check the key with a real request before saving it.
    verify: bool = True


def _provider_state(name: str) -> Dict[str, Any]:
    spec = _BY_NAME[name]
    creds = settings.provider_credentials().get(name) or {}
    key = (creds.get("api_key") or "").strip()
    return {
        **spec,
        "configured": bool(key),
        "masked": envfile.mask(key),
        "model": (creds.get("model") or spec["default_model"]),
    }


@router.get("/status", summary="What is configured, and what still needs a key")
async def setup_status(
    model_gateway: ModelGateway = Depends(get_model_gateway),
) -> Dict[str, Any]:
    """Enough for the UI to decide whether to ask for a key. No secrets."""
    providers = [_provider_state(p["name"]) for p in PROVIDERS]
    destination = envfile.target_path()
    parent = destination.parent
    try:
        writable = (destination.exists() and destination.is_file() and _writable(destination)) or (
            not destination.exists() and parent.exists() and _writable(parent)
        )
    except OSError:
        writable = False

    return {
        "configured": bool(model_gateway.has_cloud),
        "providers": providers,
        "user_name": (settings.USER_NAME or ""),
        "assistant_name": settings.ASSISTANT_NAME,
        "env_path": str(destination),
        "env_writable": bool(writable),
        # Everything still works with no key at all — commands run offline —
        # so the UI can offer to skip rather than blocking the door.
        "works_without_key": True,
    }


def _writable(path: Any) -> bool:
    import os

    return os.access(str(path), os.W_OK)


@router.post("", summary="Save an API key and/or your name")
@router.post("/", include_in_schema=False)
async def save_setup(
    body: SetupRequest,
    model_gateway: ModelGateway = Depends(get_model_gateway),
) -> Dict[str, Any]:
    """Verify, persist, and reload — without a restart."""
    updates: Dict[str, Optional[str]] = {}
    result: Dict[str, Any] = {"ok": True}

    # ------------------------------------------------------------- the name
    if body.user_name is not None:
        name = body.user_name.strip()[:60]
        updates["USER_NAME"] = name
        result["user_name"] = name

    # -------------------------------------------------------------- the key
    if body.api_key is not None and body.api_key.strip():
        provider = (body.provider or "gemini").strip().lower()
        spec = _BY_NAME.get(provider)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"I don't set up '{provider}' from here — choose one of "
                       f"{', '.join(_BY_NAME)}, or put it in .env yourself.",
            )
        key = body.api_key.strip()
        chosen = (body.model or "").strip()
        model = chosen or spec["default_model"]

        if body.verify:
            error, model = await _verify_chain(
                provider, key, model, explicit=bool(chosen)
            )
            if error:
                # 400, not 500: the key is the input that was wrong.
                raise HTTPException(status_code=400, detail=error)

        updates[spec["key_env"]] = key
        updates[spec["model_env"]] = model
        if body.use_for_vision and provider in _VISION_CAPABLE:
            updates["VISION_MODEL"] = model
        # Put the provider first in the chain, so a key pasted just now is the
        # one that answers the next message.
        order = [provider] + [p for p in settings.LLM_PROVIDER_ORDER if p != provider]
        updates["LLM_PROVIDER_ORDER"] = ",".join(order)
        result.update({"provider": provider, "model": model, "verified": bool(body.verify)})

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to save.")

    # ------------------------------------------------------- persist + apply
    persisted, saved_to, write_error = True, None, None
    try:
        saved_to = str(envfile.update_env(updates))
    except envfile.EnvWriteError as exc:
        persisted, write_error = False, str(exc)
        logger.warning("Setup could not be persisted: %s", exc)

    # Apply to the live process either way, so it works now even if the file
    # could not be written — the response says which happened.
    #
    # Deliberately NOT reload_settings(): that rebinds the module-level name,
    # while every other module imported the object itself. Mutating the shared
    # object in place is what the rest of the process actually sees.
    _apply(updates)
    model_gateway.rebuild()

    result.update({
        "persisted": persisted,
        "saved_to": saved_to,
        "restart_required": False,
        "configured": bool(model_gateway.has_cloud),
    })
    if write_error:
        result["warning"] = (
            f"Applied for this session, but {write_error}. It will be forgotten "
            "when IRIS restarts unless you add it to .env yourself."
        )
    return result


def _apply(updates: Dict[str, Optional[str]]) -> None:
    """Push values into the live settings object."""
    for key, value in updates.items():
        if not hasattr(settings, key):
            continue
        if key == "LLM_PROVIDER_ORDER":
            setattr(settings, key, [p for p in (value or "").split(",") if p])
        else:
            setattr(settings, key, value)


async def _verify_once(provider: str, key: str, model: str) -> Optional[str]:
    """Ask the provider something tiny. Returns the raw failure, or None.

    A live check is the whole point: a key that is merely *stored* looks
    identical to one that works until the first real question fails. The error
    comes back raw so the caller can tell a dead model from a dead network —
    only one of those is worth retrying with a different name.
    """
    creds = dict(settings.provider_credentials().get(provider) or {})
    creds["api_key"] = key
    creds["model"] = model
    candidate = build_provider(provider, creds)
    started = time.perf_counter()
    try:
        await candidate.generate(
            "Reply with the single word: ok",
            system_prompt="Answer with one word.",
            max_tokens=5,
            temperature=0.0,
        )
        logger.info("Setup verified %s/%s in %.0f ms", provider, model,
                    (time.perf_counter() - started) * 1000)
        return None
    except LLMProviderError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - any failure here is the user's answer
        return f"{provider}: could not reach it ({exc})"
    finally:
        try:
            await candidate.close()
        except Exception:  # noqa: BLE001
            pass


async def _verify_chain(
    provider: str, key: str, model: str, *, explicit: bool
) -> tuple[Optional[str], str]:
    """Verify ``model``, falling back to others when only the name is wrong.

    Returns ``(human_error_or_None, model_that_worked)``. A model the caller
    chose is never silently swapped: being quietly moved off the model you
    asked for is worse than being told it is gone.
    """
    failure = await _verify_once(provider, key, model)
    if failure is None:
        return None, model
    if explicit or not _is_model_error(failure):
        return _explain(provider, failure), model

    for candidate in _FALLBACK_MODELS.get(provider, []):
        if candidate == model:
            continue
        logger.info("Setup: %s has no %s, trying %s", provider, model, candidate)
        retry = await _verify_once(provider, key, candidate)
        if retry is None:
            return None, candidate
        if not _is_model_error(retry):
            # The network or the key died mid-sweep; report that, not a
            # roll-call of models we never really got to ask about.
            return _explain(provider, retry), model

    return _explain(provider, failure), model


def _is_transport_error(lowered: str) -> bool:
    """The request never got an answer — nothing was judged."""
    return (
        "connection failed" in lowered
        or "transport error" in lowered
        or "could not reach" in lowered
        or "timeout" in lowered
        or "timed out" in lowered
    )


def _is_auth_error(lowered: str) -> bool:
    return (
        "401" in lowered
        or "403" in lowered
        or "api key" in lowered
        or "unauthor" in lowered
        or "permission denied" in lowered
    )


def _is_model_error(raw: str) -> bool:
    """The key was accepted; the model name was not.

    Checked against the other two first, because a dead network loses to a
    substring every time: the request URL carries the model name, so a plain
    "connection failed" reply used to match a bare ``"model"`` test and tell
    someone with the Wi-Fi off that Google did not have that model. The
    message a person acts on has to name the thing that actually broke.
    """
    lowered = raw.lower()
    if _is_transport_error(lowered) or _is_auth_error(lowered):
        return False
    return (
        "404" in lowered
        or "not found" in lowered
        or "unknown model" in lowered
        or "is not supported" in lowered
        or "does not exist" in lowered
    )


def _explain(provider: str, raw: str) -> str:
    """Turn a provider's error into something worth reading."""
    lowered = raw.lower()
    label = _BY_NAME[provider]["label"]
    if _is_transport_error(lowered):
        return (f"Could not reach {label} — this machine looks offline. "
                "Check the connection and press Connect again; the key is fine.")
    if _is_auth_error(lowered):
        # The hint keeps its own capitals: "AIza" is case-sensitive, and
        # telling someone to look for "aiza" sends them hunting for a key
        # they already have.
        return (f"{label} refused that key. Check you copied all of it, and that it is "
                f"a {label} key. {_BY_NAME[provider]['hint']}.")
    if "429" in lowered or "rate" in lowered or "quota" in lowered:
        return (f"{label} accepted the key but is rate-limiting right now. "
                "It will most likely work in a minute — save it and try a message.")
    if _is_model_error(raw):
        return (f"The key works, but {label} no longer offers any of the models "
                "IRIS knows to ask for. Set the model name in .env "
                f"({_BY_NAME[provider]['model_env']}) and restart.")
    return f"{label} rejected the key: {raw[:200]}"
