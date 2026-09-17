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
        model = (body.model or "").strip() or spec["default_model"]

        if body.verify:
            error = await _verify(provider, key, model)
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


async def _verify(provider: str, key: str, model: str) -> Optional[str]:
    """Ask the provider something tiny. Returns a human reason, or None.

    A live check is the whole point: a key that is merely *stored* looks
    identical to one that works until the first real question fails.
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
        logger.info("Setup verified %s in %.0f ms", provider, (time.perf_counter() - started) * 1000)
        return None
    except LLMProviderError as exc:
        return _explain(provider, str(exc))
    except Exception as exc:  # noqa: BLE001 - any failure here is the user's answer
        return f"Could not reach {provider}: {exc}"
    finally:
        try:
            await candidate.close()
        except Exception:  # noqa: BLE001
            pass


def _explain(provider: str, raw: str) -> str:
    """Turn a provider's error into something worth reading."""
    lowered = raw.lower()
    label = _BY_NAME[provider]["label"]
    if "401" in lowered or "403" in lowered or "api key" in lowered or "unauthor" in lowered:
        # The hint keeps its own capitals: "AIza" is case-sensitive, and
        # telling someone to look for "aiza" sends them hunting for a key
        # they already have.
        return (f"{label} refused that key. Check you copied all of it, and that it is "
                f"a {label} key. {_BY_NAME[provider]['hint']}.")
    if "404" in lowered or "not found" in lowered or "model" in lowered:
        return (f"The key looks fine, but {label} does not have that model. "
                "Leave the model box empty to use the default.")
    if "429" in lowered or "rate" in lowered or "quota" in lowered:
        return (f"{label} accepted the key but is rate-limiting right now. "
                "It will most likely work in a minute — save it and try a message.")
    if "connection" in lowered or "timeout" in lowered or "timed out" in lowered:
        return f"Could not reach {label} — check this machine is online."
    return f"{label} rejected the key: {raw[:200]}"
