"""Application configuration management using Pydantic Settings."""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """System-wide configuration settings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    APP_NAME: str = "NOVA"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    # Persistence
    DATABASE_URL: str = "sqlite+aiosqlite:///./nova.db"

    # LLM Mode & Gateway
    LLM_MODE: str = "mock"  # "mock", "local", "auto"
    DEFAULT_PROVIDER: str = "mock"
    DEFAULT_MODEL_MODE: str = "reasoning"  # "reasoning", "fast"

    # Local OpenAI-Compatible LLM Server
    LOCAL_LLM_BASE_URL: str = "http://localhost:8000/v1"
    LOCAL_LLM_API_KEY: str = "EMPTY"
    LOCAL_LLM_MODEL: Optional[str] = None
    LOCAL_LLM_TIMEOUT_SECONDS: float = 60.0

    # Remote Cloud LLM Server (Future)
    REMOTE_LLM_URL: Optional[str] = "https://api.openai.com/v1"
    REMOTE_LLM_MODEL: Optional[str] = "gpt-4o"
    REMOTE_LLM_API_KEY: Optional[str] = None

    # Model Router Defaults
    DEFAULT_MODEL: str = "mock-model"
    FAST_MODEL: str = "mock-fast"
    REASONING_MODEL: str = "mock-reasoning"
    VISION_MODEL: Optional[str] = None  # None indicates vision capability unavailable
    CODING_MODEL: str = "mock-coding"

    # Safety Guards & Limits
    MAX_PLANNING_ITERATIONS: int = 5
    MAX_TOOL_CALLS: int = 10
    PER_TOOL_TIMEOUT_SECONDS: float = 10.0
    TOTAL_TASK_TIMEOUT_SECONDS: float = 60.0

    # Security
    AUTO_APPROVE_LOW_RISK: bool = True


settings = Settings()
