"""Tests for Hindi/Hinglish localization: detector thresholds, smalltalk
variants, Hinglish memory commands, and deterministic ack localization."""

from iris.app.agent.smalltalk import match_smalltalk
from iris.app.language.detector import LanguageDetector
from iris.app.language.localize import localize_ack
from iris.app.language.models import LanguageCode, LanguageStyle
from iris.app.memory.extractor import MemoryExtractor


# ----------------------------------------------------------------------
# Detector thresholds — no more Hinglish false positives
# ----------------------------------------------------------------------

def test_plain_english_with_one_ambiguous_token_stays_english():
    detector = LanguageDetector()
    prompts = [
        "I will do it tomorrow, na",          # lone "na"
        "let them ho about their business",   # lone "ho"
        "put it on par with the rest please",  # lone "par"
    ]
    for prompt in prompts:
        res = detector.detect(prompt)
        assert res.language == LanguageCode.EN, f"Failed for: {prompt}"
        assert res.style == LanguageStyle.ENGLISH


def test_removed_ambiguous_words_no_longer_trigger_hinglish():
    detector = LanguageDetector()
    for prompt in ("that homework was easy", "pick me up from school please"):
        res = detector.detect(prompt)
        assert res.language == LanguageCode.EN, f"Failed for: {prompt}"


def test_two_marker_hits_classify_hinglish():
    detector = LanguageDetector()
    res = detector.detect("mera pc slow ho raha hai")
    assert res.language == LanguageCode.HINGLISH
    assert res.style in (LanguageStyle.HINGLISH, LanguageStyle.MIXED)


def test_single_unambiguous_word_still_classifies_hinglish():
    detector = LanguageDetector()
    for prompt in ("youtube kholo", "kya time hua", "notifications band karo"):
        res = detector.detect(prompt)
        assert res.language == LanguageCode.HINGLISH, f"Failed for: {prompt}"


# ----------------------------------------------------------------------
# Smalltalk style variants
# ----------------------------------------------------------------------

def test_smalltalk_hinglish_greeting_variant():
    reply = match_smalltalk("hello", style=LanguageStyle.HINGLISH)
    assert reply is not None
    assert "hoon" in reply.lower()


def test_smalltalk_hindi_thanks_variant():
    reply = match_smalltalk("thank you", style=LanguageStyle.HINDI)
    assert reply is not None
    assert any("ऀ" <= ch <= "ॿ" for ch in reply)


def test_smalltalk_hinglish_how_are_you():
    reply = match_smalltalk("kaise ho", style=LanguageStyle.HINGLISH)
    assert reply is not None
    assert "batao" in reply.lower() or "badhiya" in reply.lower()


def test_smalltalk_mixed_maps_to_hinglish_and_string_styles_accepted():
    enum_reply = match_smalltalk("hello", style=LanguageStyle.MIXED)
    str_reply = match_smalltalk("hello", style="HINGLISH")
    assert enum_reply == str_reply
    assert "hoon" in (str_reply or "").lower()


def test_smalltalk_devanagari_triggers():
    greeting = match_smalltalk("नमस्ते IRIS", style=LanguageStyle.HINDI)
    assert greeting is not None and "नमस्ते" in greeting
    thanks = match_smalltalk("धन्यवाद", style=LanguageStyle.HINDI)
    assert thanks is not None and any("ऀ" <= ch <= "ॿ" for ch in thanks)
    shukriya = match_smalltalk("शुक्रिया", style=LanguageStyle.HINGLISH)
    assert shukriya is not None


def test_smalltalk_defaults_to_english():
    reply = match_smalltalk("hello")
    assert reply is not None
    assert "What can I do for you?" in reply


def test_smalltalk_english_only_rule_falls_back_for_hindi_style():
    reply = match_smalltalk("tell me a joke", style=LanguageStyle.HINDI)
    assert reply is not None  # falls back to the English joke pool


# ----------------------------------------------------------------------
# Memory extractor Hinglish alternations
# ----------------------------------------------------------------------

def test_extractor_yaad_rakho_remember():
    cmd, payload = MemoryExtractor.parse_command("yaad rakho mera budget 15000")
    assert cmd == "remember"
    assert payload["key"] == "budget"
    assert "15000" in str(payload["value"]).replace(",", "")


def test_extractor_yaad_rakhna_suffix_form():
    cmd, payload = MemoryExtractor.parse_command("mera robot budget 20000 yaad rakhna")
    assert cmd == "remember"
    assert payload["key"] == "robot_budget"


def test_extractor_bhool_jao_forget():
    for phrase in ("bhool jao mera budget", "mera budget bhul jao"):
        cmd, payload = MemoryExtractor.parse_command(phrase)
        assert cmd == "forget", f"Failed for: {phrase}"
        assert payload["key"] == "budget"


def test_extractor_kya_hai_recall():
    cmd, payload = MemoryExtractor.parse_command("mera budget kya hai?")
    assert cmd == "recall"
    assert payload["key"] == "budget"


def test_extractor_english_commands_still_work():
    cmd, payload = MemoryExtractor.parse_command("remember that my robot budget is 15000")
    assert cmd == "remember"
    assert payload["key"] == "robot_budget"
    cmd, payload = MemoryExtractor.parse_command("forget my budget")
    assert cmd == "forget"
    cmd, payload = MemoryExtractor.parse_command("what is my budget?")
    assert cmd == "recall"


# ----------------------------------------------------------------------
# localize_ack
# ----------------------------------------------------------------------

def test_localize_ack_basic_hinglish_mappings():
    assert localize_ack("Opened YouTube.", LanguageStyle.HINGLISH) == "YouTube khol diya."
    assert localize_ack("Done.", LanguageStyle.HINGLISH) == "Ho gaya."
    assert localize_ack("Timer set for 5 minutes.", LanguageStyle.HINGLISH) == (
        "Timer laga diya — 5 minutes."
    )
    assert localize_ack("Screenshot saved.", LanguageStyle.HINGLISH) == "Screenshot le liya."
    assert localize_ack("Turned off wifi.", LanguageStyle.HINGLISH) == "wifi band kar diya."


def test_localize_ack_hindi_mappings():
    assert localize_ack("Done.", LanguageStyle.HINDI) == "हो गया।"
    assert localize_ack("Opened YouTube.", LanguageStyle.HINDI) == "YouTube खोल दिया।"


def test_localize_ack_accepts_string_style_and_mixed():
    assert localize_ack("Done.", "HINGLISH") == "Ho gaya."
    assert localize_ack("Done.", LanguageStyle.MIXED) == "Ho gaya."


def test_localize_ack_unknown_phrase_passes_through():
    original = "Rebooted the flux capacitor."
    assert localize_ack(original, LanguageStyle.HINGLISH) == original


def test_localize_ack_english_style_untouched():
    assert localize_ack("Opened YouTube.", LanguageStyle.ENGLISH) == "Opened YouTube."
    assert localize_ack("Opened YouTube.", None) == "Opened YouTube."
