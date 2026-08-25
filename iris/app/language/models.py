"""Domain models for Language Intelligence layer."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class LanguageCode(str, Enum):
    """Supported language codes."""
    EN = "en"
    HI = "hi"
    HINGLISH = "hinglish"
    UNKNOWN = "unknown"


class LanguageStyle(str, Enum):
    """Classification of language style / register."""
    ENGLISH = "ENGLISH"
    HINDI = "HINDI"
    HINGLISH = "HINGLISH"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class LanguageDetectionResult(BaseModel):
    """Metadata representing the outcome of language analysis."""
    language: LanguageCode = LanguageCode.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    style: LanguageStyle = LanguageStyle.UNKNOWN
    detected_script: str = "latin"
    signals: List[str] = Field(default_factory=list)
    explicit_request: Optional[LanguageCode] = None


class LanguageContext(BaseModel):
    """Tracked language state within a conversation session."""
    current_language: LanguageCode = LanguageCode.EN
    preferred_response_language: LanguageCode = LanguageCode.EN
    recent_languages: List[LanguageCode] = Field(default_factory=list)
    explicit_request: Optional[LanguageCode] = None
    style: LanguageStyle = LanguageStyle.ENGLISH
