"""Language Intelligence Domain Package for IRIS."""

from iris.app.language.models import (
    LanguageCode,
    LanguageStyle,
    LanguageDetectionResult,
    LanguageContext,
)
from iris.app.language.detector import LanguageDetector, default_language_detector
from iris.app.language.normalizer import LanguageNormalizer, default_language_normalizer
from iris.app.language.policy import ResponseLanguagePolicy, default_response_language_policy
from iris.app.language.service import LanguageService, default_language_service

__all__ = [
    "LanguageCode",
    "LanguageStyle",
    "LanguageDetectionResult",
    "LanguageContext",
    "LanguageDetector",
    "default_language_detector",
    "LanguageNormalizer",
    "default_language_normalizer",
    "ResponseLanguagePolicy",
    "default_response_language_policy",
    "LanguageService",
    "default_language_service",
]
