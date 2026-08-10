"""Unified Language Service for Language Intelligence layer."""

from typing import Tuple, Optional
from nova.app.language.models import (
    LanguageCode,
    LanguageStyle,
    LanguageDetectionResult,
    LanguageContext,
)
from nova.app.language.detector import LanguageDetector, default_language_detector
from nova.app.language.normalizer import LanguageNormalizer, default_language_normalizer
from nova.app.language.policy import ResponseLanguagePolicy, default_response_language_policy
from nova.app.core.logging import get_logger

logger = get_logger("language.service")


class LanguageService:
    """Orchestrates language detection, normalization, policy evaluation, and context tracking."""

    def __init__(
        self,
        detector: Optional[LanguageDetector] = None,
        normalizer: Optional[LanguageNormalizer] = None,
        policy: Optional[ResponseLanguagePolicy] = None,
    ):
        self.detector = detector or default_language_detector
        self.normalizer = normalizer or default_language_normalizer
        self.policy = policy or default_response_language_policy

    def process_input(
        self,
        text: str,
        context: Optional[LanguageContext] = None,
    ) -> Tuple[LanguageDetectionResult, str, LanguageCode, LanguageStyle]:
        """Analyze user input and return detection metadata, tool normalized text, target language, and style."""
        ctx = context or LanguageContext()

        # 1. Detect language, script, style, and explicit directive
        detection = self.detector.detect(text)

        # 2. Update context explicit request if directive present
        if detection.explicit_request:
            ctx.explicit_request = detection.explicit_request

        # 3. Determine target response language & style via policy
        target_lang, target_style = self.policy.determine_response_language(detection, ctx)

        # 4. Produce tool-normalized expression
        normalized = self.normalizer.normalize_tool_expression(text)

        logger.info(
            f"Language Intelligence: detected={detection.language.value} (conf={detection.confidence}), "
            f"target_response={target_lang.value}, style={target_style.value}"
        )

        return detection, normalized, target_lang, target_style

    def update_context(
        self,
        context: LanguageContext,
        detection: LanguageDetectionResult,
        response_lang: LanguageCode,
    ) -> LanguageContext:
        """Update context state for ongoing conversation tracking."""
        if detection.language != LanguageCode.UNKNOWN:
            context.current_language = detection.language
            context.recent_languages.append(detection.language)
            if len(context.recent_languages) > 10:
                context.recent_languages.pop(0)

        context.preferred_response_language = response_lang
        context.style = detection.style
        if detection.explicit_request:
            context.explicit_request = detection.explicit_request

        return context


default_language_service = LanguageService()
