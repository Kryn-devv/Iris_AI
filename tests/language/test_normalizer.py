"""Tests for LanguageNormalizer."""

import pytest
from nova.app.language.normalizer import LanguageNormalizer


def test_hinglish_calculator_normalization():
    normalizer = LanguageNormalizer()
    res = normalizer.normalize_tool_expression("25 ko 40 se multiply karo")
    assert "25 * 40" in res or res == "25 * 40"


def test_devanagari_hindi_calculator_normalization():
    normalizer = LanguageNormalizer()
    res = normalizer.normalize_tool_expression("25 को 40 से गुणा करो")
    assert "25 * 40" in res or res == "25 * 40"


def test_devanagari_digit_conversion():
    normalizer = LanguageNormalizer()
    converted = normalizer.convert_devanagari_digits("२५ को ४० से गुणा करो")
    assert "25" in converted
    assert "40" in converted


def test_standard_expression_passthrough():
    normalizer = LanguageNormalizer()
    assert normalizer.normalize_tool_expression("78 * 23 * 7") == "78 * 23 * 7"
