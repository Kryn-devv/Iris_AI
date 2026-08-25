"""Tests for voice utilities."""

from iris.app.voice.service import sanitize_for_speech, strip_wake_word


def test_sanitize_strips_markdown_and_urls():
    text = "**Done!** See https://example.com/very/long/path and `code`"
    cleaned = sanitize_for_speech(text)
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "https://" not in cleaned
    assert "a link" in cleaned


def test_sanitize_truncates_on_sentence():
    text = ("This is a sentence. " * 60).strip()
    cleaned = sanitize_for_speech(text, max_chars=100)
    assert len(cleaned) <= 101
    assert cleaned.endswith(".") or cleaned.endswith("…")


def test_strip_wake_word_variants():
    assert strip_wake_word("hey iris open youtube") == (True, "open youtube")
    assert strip_wake_word("Iris, what's the time?") == (True, "what's the time?")
    assert strip_wake_word("ok iris") == (True, "")
    assert strip_wake_word("open youtube") == (False, "open youtube")


def test_strip_wake_word_longest_first():
    # "hey iris" must win over bare "iris" so no leading word remains.
    woken, rest = strip_wake_word("hey iris play music", ["iris", "hey iris"])
    assert woken and rest == "play music"
