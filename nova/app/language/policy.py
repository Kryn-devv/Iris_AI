"""Response Language Policy for governing output language selection and style."""

from nova.app.language.models import (
    LanguageCode,
    LanguageStyle,
    LanguageDetectionResult,
    LanguageContext,
)


class ResponseLanguagePolicy:
    """Determines target response language based on user detection, explicit directives, and context."""

    def determine_response_language(
        self,
        detection: LanguageDetectionResult,
        context: LanguageContext,
    ) -> Tuple_LanguageCode_Style:
        """Evaluate policy rules and return (target_language, style)."""
        # Rule 1: Explicit user request takes highest precedence
        if detection.explicit_request:
            target_lang = detection.explicit_request
            style = self._map_language_to_style(target_lang)
            return target_lang, style

        # Rule 2: Persistent context explicit request
        if context.explicit_request:
            target_lang = context.explicit_request
            style = self._map_language_to_style(target_lang)
            return target_lang, style

        # Rule 3: Detected user language
        if detection.language != LanguageCode.UNKNOWN:
            return detection.language, detection.style

        # Rule 4: Conversation preference context fallback
        if context.preferred_response_language != LanguageCode.UNKNOWN:
            return context.preferred_response_language, context.style

        # Rule 5: Default fallback to English
        return LanguageCode.EN, LanguageStyle.ENGLISH

    @staticmethod
    def _map_language_to_style(code: LanguageCode) -> LanguageStyle:
        if code == LanguageCode.HI:
            return LanguageStyle.HINDI
        elif code == LanguageCode.HINGLISH:
            return LanguageStyle.HINGLISH
        elif code == LanguageCode.EN:
            return LanguageStyle.ENGLISH
        return LanguageStyle.UNKNOWN


# Tuple return type helper
Tuple_LanguageCode_Style = tuple[LanguageCode, LanguageStyle]

default_response_language_policy = ResponseLanguagePolicy()
