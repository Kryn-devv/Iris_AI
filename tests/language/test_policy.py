"""Tests for ResponseLanguagePolicy."""

import pytest
from iris.app.language.models import (
    LanguageCode,
    LanguageStyle,
    LanguageDetectionResult,
    LanguageContext,
)
from iris.app.language.policy import ResponseLanguagePolicy


def test_policy_explicit_directive_override():
    policy = ResponseLanguagePolicy()
    det = LanguageDetectionResult(
        language=LanguageCode.EN,
        confidence=0.9,
        style=LanguageStyle.ENGLISH,
        explicit_request=LanguageCode.HI,
    )
    ctx = LanguageContext()
    lang, style = policy.determine_response_language(det, ctx)
    assert lang == LanguageCode.HI
    assert style == LanguageStyle.HINDI


def test_policy_detected_language_match():
    policy = ResponseLanguagePolicy()
    det = LanguageDetectionResult(
        language=LanguageCode.HINGLISH,
        confidence=0.8,
        style=LanguageStyle.HINGLISH,
    )
    ctx = LanguageContext()
    lang, style = policy.determine_response_language(det, ctx)
    assert lang == LanguageCode.HINGLISH
    assert style == LanguageStyle.HINGLISH


def test_policy_context_fallback():
    policy = ResponseLanguagePolicy()
    det = LanguageDetectionResult(
        language=LanguageCode.UNKNOWN,
        confidence=0.2,
        style=LanguageStyle.UNKNOWN,
    )
    ctx = LanguageContext(preferred_response_language=LanguageCode.HI, style=LanguageStyle.HINDI)
    lang, style = policy.determine_response_language(det, ctx)
    assert lang == LanguageCode.HI
    assert style == LanguageStyle.HINDI
