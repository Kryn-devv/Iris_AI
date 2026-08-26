"""Application configuration for IRIS.

Everything is environment-driven (``.env`` or real environment variables) with
safe, working defaults so a fresh clone runs with zero configuration. No API
key is ever required: with no keys present IRIS falls back to its deterministic
local command engine plus the offline mock reasoner.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from iris.app.core import paths


def _split_csv(value: Any) -> List[str]:
    """Parse a list setting from JSON, a CSV string, or an existing list."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


class Settings(BaseSettings):
    """System-wide configuration settings."""

    # ``.env`` is resolved to ABSOLUTE paths: IRIS is started at login by a
    # registry Run key / LaunchAgent whose working directory is not the project
    # folder, and a relative path there would silently find nothing.
    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in paths.env_file_candidates()),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ general
    APP_NAME: str = "IRIS"
    ASSISTANT_NAME: str = "Iris"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False
    LOG_TO_FILE: bool = True
    DEBUG: bool = False

    HOST: str = "127.0.0.1"
    PORT: int = 8756
    # Bind to 0.0.0.0 so a phone on the same Wi-Fi can reach IRIS.
    ALLOW_LAN_ACCESS: bool = False
    CORS_ORIGINS: List[str] = Field(default_factory=lambda: ["*"])

    # -------------------------------------------------------------- persistence
    DATABASE_URL: str = ""

    # ------------------------------------------------------- LLM routing (free)
    # Operating mode:
    #   off    -> never call any network model; deterministic engine only
    #   mock   -> offline deterministic reasoner (default, zero config)
    #   cloud  -> only routed free/cloud providers
    #   auto   -> routed providers when a key is present, otherwise mock
    LLM_MODE: str = "auto"

    # Ordered preference list. The router tries each provider in turn and
    # remembers failures with a circuit breaker.
    LLM_PROVIDER_ORDER: List[str] = Field(
        default_factory=lambda: [
            "openrouter",
            "groq",
            "gemini",
            "cerebras",
            "mistral",
            "together",
            "github_models",
            "huggingface",
            "openai_compat",
        ]
    )
    LLM_FREE_ONLY: bool = True
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_RETRIES: int = 2
    LLM_CIRCUIT_BREAK_SECONDS: float = 120.0
    LLM_TEMPERATURE: float = 0.6
    LLM_MAX_TOKENS: int = 1024
    LLM_STREAM: bool = True

    # OpenRouter (https://openrouter.ai) - large catalogue of ':free' models
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_MODEL: str = "z-ai/glm-5.2:free"
    OPENROUTER_APP_URL: str = "https://github.com/Kryn-devv/Iris_AI"
    OPENROUTER_APP_TITLE: str = "IRIS Desktop Assistant"

    # Groq (very fast free tier)
    GROQ_API_KEY: Optional[str] = None
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # Google AI Studio (free tier, OpenAI-compatible endpoint)
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    GEMINI_MODEL: str = "gemini-flash-latest"

    # Cerebras inference (free tier)
    CEREBRAS_API_KEY: Optional[str] = None
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"
    CEREBRAS_MODEL: str = "llama-3.3-70b"

    # Mistral (free experimental tier)
    MISTRAL_API_KEY: Optional[str] = None
    MISTRAL_BASE_URL: str = "https://api.mistral.ai/v1"
    MISTRAL_MODEL: str = "mistral-small-latest"

    # Together AI (free tier models)
    TOGETHER_API_KEY: Optional[str] = None
    TOGETHER_BASE_URL: str = "https://api.together.xyz/v1"
    TOGETHER_MODEL: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"

    # GitHub Models (free with a GitHub PAT)
    GITHUB_MODELS_API_KEY: Optional[str] = None
    GITHUB_MODELS_BASE_URL: str = "https://models.inference.ai.azure.com"
    GITHUB_MODELS_MODEL: str = "gpt-4o-mini"

    # Hugging Face router (free credits)
    HUGGINGFACE_API_KEY: Optional[str] = None
    HUGGINGFACE_BASE_URL: str = "https://router.huggingface.co/v1"
    HUGGINGFACE_MODEL: str = "meta-llama/Llama-3.1-8B-Instruct"

    # Any other OpenAI-compatible endpoint (self-hosted gateway, LiteLLM, ...)
    OPENAI_COMPAT_API_KEY: Optional[str] = None
    OPENAI_COMPAT_BASE_URL: Optional[str] = None
    OPENAI_COMPAT_MODEL: Optional[str] = None

    # Capability -> model overrides (blank falls back to the provider default)
    FAST_MODEL: Optional[str] = None
    REASONING_MODEL: Optional[str] = None
    CODING_MODEL: Optional[str] = None
    VISION_MODEL: Optional[str] = None
    DEFAULT_MODEL: str = "iris-local"
    DEFAULT_MODEL_MODE: str = "reasoning"

    # ---------------------------------------------------- deterministic NLU
    # When True, a confidently matched local command runs without any model
    # call at all: "open youtube" never needs the network.
    NLU_ENABLED: bool = True
    NLU_MIN_CONFIDENCE: float = 0.62
    NLU_FUZZY_THRESHOLD: int = 82

    # ---------------------------------------------------------- agent limits
    MAX_PLANNING_ITERATIONS: int = 6
    MAX_TOOL_CALLS: int = 16
    PER_TOOL_TIMEOUT_SECONDS: float = 20.0
    TOTAL_TASK_TIMEOUT_SECONDS: float = 120.0

    # -------------------------------------------------------------- security
    AUTO_APPROVE_LOW_RISK: bool = True
    AUTO_APPROVE_DESKTOP_ACTIONS: bool = True
    ALLOW_HIGH_RISK_ACTIONS: bool = False
    ALLOW_SHELL_TOOL: bool = False
    ALLOW_POWER_ACTIONS: bool = False
    REQUIRE_CONFIRM_FOR_DELETE: bool = True

    # Filesystem sandbox. Empty means "the default workspace plus common user
    # folders"; resolved lazily by iris.app.core.security.
    FS_ALLOWED_ROOTS: List[str] = Field(default_factory=list)
    FS_DENIED_PATTERNS: List[str] = Field(
        default_factory=lambda: [
            "**/.ssh/**",
            "**/.aws/**",
            "**/.gnupg/**",
            "**/*.pem",
            "**/*.key",
            "**/id_rsa*",
            "**/.env",
            "**/AppData/Roaming/Microsoft/Crypto/**",
        ]
    )
    FS_MAX_READ_BYTES: int = 2_000_000
    FS_MAX_WRITE_BYTES: int = 20_000_000

    # Remote access token. Auto-generated on first run when left blank.
    API_TOKEN: Optional[str] = None
    REQUIRE_AUTH: bool = False
    RATE_LIMIT_PER_MINUTE: int = 240

    # ----------------------------------------------------------------- voice
    VOICE_ENABLED: bool = True
    WAKE_WORDS: List[str] = Field(default_factory=lambda: ["iris", "hey iris", "ok iris"])
    WAKE_WORD_ENABLED: bool = True
    STT_ENGINE: str = "auto"      # auto | faster_whisper | vosk | google_free | browser
    STT_MODEL: str = "base.en"
    STT_LANGUAGE: str = "auto"
    TTS_ENGINE: str = "auto"      # auto | piper | pyttsx3 | edge | gtts | browser
    TTS_VOICE: str = ""
    TTS_RATE: int = 180
    TTS_VOLUME: float = 0.9
    VOICE_BARGE_IN: bool = True
    VAD_AGGRESSIVENESS: int = 2
    MIC_SAMPLE_RATE: int = 16000
    SPEAK_RESPONSES: bool = True

    # ------------------------------------------------------------ automation
    SCHEDULER_ENABLED: bool = True
    HOTKEYS_ENABLED: bool = True
    SUMMON_HOTKEY: str = "ctrl+alt+space"
    PUSH_TO_TALK_HOTKEY: str = "ctrl+alt+i"
    CLIPBOARD_WATCHER_ENABLED: bool = False

    # --------------------------------------------------------------- bridges
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_ALLOWED_USER_IDS: List[str] = Field(default_factory=list)
    TELEGRAM_POLL_INTERVAL: float = 2.0
    TUNNEL_PROVIDER: str = "none"   # none | cloudflared | ngrok

    # ------------------------------------------------------------- web tools
    WEB_SEARCH_PROVIDER: str = "duckduckgo"   # duckduckgo | searx | wikipedia
    SEARX_BASE_URL: str = "https://searx.be"
    WEB_FETCH_MAX_BYTES: int = 1_500_000
    WEB_USER_AGENT: str = "Mozilla/5.0 (compatible; IrisAssistant/1.0)"
    WEATHER_UNITS: str = "metric"
    DEFAULT_LOCATION: str = ""
    NEWS_FEEDS: List[str] = Field(
        default_factory=lambda: [
            "https://feeds.bbci.co.uk/news/rss.xml",
            "https://news.google.com/rss",
            "https://hnrss.org/frontpage",
        ]
    )

    # ------------------------------------------------------------------- UI
    UI_THEME: str = "dark"
    UI_ACCENT: str = "#5eead4"
    UI_HOLOGRAM: bool = True
    UI_HOLOGRAM_QUALITY: str = "high"   # low | medium | high
    UI_REDUCED_MOTION: bool = False
    OPEN_BROWSER_ON_START: bool = True

    # -------------------------------------------------------------- desktop
    DESKTOP_MODE: str = "browser"   # browser | webview | headless
    TRAY_ENABLED: bool = True
    START_MINIMIZED: bool = False
    AUTOSTART_ENABLED: bool = False

    # ------------------------------------------------------------ validators
    @field_validator(
        "CORS_ORIGINS",
        "LLM_PROVIDER_ORDER",
        "WAKE_WORDS",
        "FS_ALLOWED_ROOTS",
        "FS_DENIED_PATTERNS",
        "TELEGRAM_ALLOWED_USER_IDS",
        "NEWS_FEEDS",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, value: Any) -> List[str]:
        return _split_csv(value)

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _default_database_url(cls, value: Any) -> str:
        text = str(value or "").strip()
        return text or paths.default_database_url()

    @field_validator("LLM_MODE", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> str:
        text = str(value or "auto").strip().lower()
        aliases = {"local": "cloud", "remote": "cloud", "none": "off", "disabled": "off"}
        return aliases.get(text, text)

    # ------------------------------------------------------------- helpers
    @property
    def bind_host(self) -> str:
        """Effective bind address, widened when LAN access is enabled."""
        return "0.0.0.0" if self.ALLOW_LAN_ACCESS else self.HOST

    @property
    def base_url(self) -> str:
        """Local URL the UI and desktop shell should open."""
        host = "127.0.0.1" if self.HOST in ("0.0.0.0", "::") else self.HOST
        return f"http://{host}:{self.PORT}"

    def provider_credentials(self) -> dict[str, dict[str, Optional[str]]]:
        """Map of provider name -> {api_key, base_url, model}."""
        return {
            "openrouter": {
                "api_key": self.OPENROUTER_API_KEY,
                "base_url": self.OPENROUTER_BASE_URL,
                "model": self.OPENROUTER_MODEL,
            },
            "groq": {
                "api_key": self.GROQ_API_KEY,
                "base_url": self.GROQ_BASE_URL,
                "model": self.GROQ_MODEL,
            },
            "gemini": {
                "api_key": self.GEMINI_API_KEY,
                "base_url": self.GEMINI_BASE_URL,
                "model": self.GEMINI_MODEL,
            },
            "cerebras": {
                "api_key": self.CEREBRAS_API_KEY,
                "base_url": self.CEREBRAS_BASE_URL,
                "model": self.CEREBRAS_MODEL,
            },
            "mistral": {
                "api_key": self.MISTRAL_API_KEY,
                "base_url": self.MISTRAL_BASE_URL,
                "model": self.MISTRAL_MODEL,
            },
            "together": {
                "api_key": self.TOGETHER_API_KEY,
                "base_url": self.TOGETHER_BASE_URL,
                "model": self.TOGETHER_MODEL,
            },
            "github_models": {
                "api_key": self.GITHUB_MODELS_API_KEY,
                "base_url": self.GITHUB_MODELS_BASE_URL,
                "model": self.GITHUB_MODELS_MODEL,
            },
            "huggingface": {
                "api_key": self.HUGGINGFACE_API_KEY,
                "base_url": self.HUGGINGFACE_BASE_URL,
                "model": self.HUGGINGFACE_MODEL,
            },
            "openai_compat": {
                "api_key": self.OPENAI_COMPAT_API_KEY,
                "base_url": self.OPENAI_COMPAT_BASE_URL,
                "model": self.OPENAI_COMPAT_MODEL,
            },
        }

    def configured_providers(self) -> List[str]:
        """Providers that have enough configuration to be attempted."""
        creds = self.provider_credentials()
        out: List[str] = []
        for name in self.LLM_PROVIDER_ORDER:
            info = creds.get(name)
            if not info:
                continue
            if info.get("api_key") and (name != "openai_compat" or info.get("base_url")):
                out.append(name)
        return out

    def capability_model(self, capability: str) -> Optional[str]:
        """Model override for a capability tag, if the user configured one."""
        return {
            "fast": self.FAST_MODEL,
            "reasoning": self.REASONING_MODEL,
            "coding": self.CODING_MODEL,
            "vision": self.VISION_MODEL,
        }.get(capability.lower())


settings = Settings()


def reload_settings() -> Settings:
    """Re-read configuration from disk/environment (used by the settings API)."""
    global settings
    paths.reset_cache()
    settings = Settings(_env_file=tuple(str(p) for p in paths.env_file_candidates()))
    return settings


def loaded_env_files() -> List[str]:
    """The ``.env`` files that were found and applied, for startup diagnostics."""
    return [str(p) for p in paths.existing_env_files()]
