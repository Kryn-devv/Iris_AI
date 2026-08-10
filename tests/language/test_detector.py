"""Tests for LanguageDetector."""

import pytest
from nova.app.language.models import LanguageCode, LanguageStyle
from nova.app.language.detector import LanguageDetector


def test_english_detection():
    detector = LanguageDetector()
    res = detector.detect("Hello NOVA, how are you today?")
    assert res.language == LanguageCode.EN
    assert res.style == LanguageStyle.ENGLISH
    assert res.detected_script == "latin"
    assert res.confidence >= 0.8


def test_hindi_devanagari_detection():
    detector = LanguageDetector()
    res = detector.detect("नमस्ते नोवा, आप कैसे हैं?")
    assert res.language == LanguageCode.HI
    assert res.style == LanguageStyle.HINDI
    assert res.detected_script == "devanagari"
    assert res.confidence >= 0.7


def test_hinglish_detection():
    detector = LanguageDetector()
    prompts = [
        "bhai kya haal hai",
        "kal mujhe school ke liye ek presentation banana hai",
        "mujhe ye samjha do",
        "mera pc slow ho raha hai",
    ]
    for prompt in prompts:
        res = detector.detect(prompt)
        assert res.language == LanguageCode.HINGLISH, f"Failed for: {prompt}"
        assert res.style in (LanguageStyle.HINGLISH, LanguageStyle.MIXED)


def test_mixed_language_detection():
    detector = LanguageDetector()
    res = detector.detect("Can you mujhe ye explain kar sakte ho?")
    assert res.language in (LanguageCode.HINGLISH, LanguageCode.EN)
    assert res.style in (LanguageStyle.HINGLISH, LanguageStyle.MIXED)


def test_explicit_language_switching_directives():
    detector = LanguageDetector()
    
    res_hi = detector.detect("Explain recursion in Hindi")
    assert res_hi.explicit_request == LanguageCode.HI

    res_en = detector.detect("Isko English mein explain karo")
    assert res_en.explicit_request == LanguageCode.EN

    res_hinglish = detector.detect("Hinglish mein samjha")
    assert res_hinglish.explicit_request == LanguageCode.HINGLISH


def test_ambiguous_low_confidence_input():
    detector = LanguageDetector()
    res = detector.detect("12345 !!! ???")
    assert res.language == LanguageCode.UNKNOWN
    assert res.confidence <= 0.5
