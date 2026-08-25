"""Tests for the deterministic NLU intent engine."""

import pytest

from iris.app.nlu.engine import IntentEngine
from iris.app.nlu.rules import normalize_command, parse_duration_seconds, parse_number


@pytest.fixture()
def engine() -> IntentEngine:
    return IntentEngine()


# ---------------------------------------------------------------- normalize
def test_normalize_strips_wake_and_politeness():
    assert normalize_command("Hey Iris, please open YouTube now!") == "open youtube"
    assert normalize_command("could you take a screenshot please") == "take a screenshot"
    assert normalize_command("  OPEN NOTEPAD.  ") == "open notepad"


def test_parse_number_words():
    assert parse_number("ten") == 10
    assert parse_number("2.5") == 2.5
    assert parse_number("banana") is None


def test_parse_duration():
    assert parse_duration_seconds("10", "minutes") == 600
    assert parse_duration_seconds("two", "hours") == 7200
    assert parse_duration_seconds("30", "secs") == 30
    assert parse_duration_seconds("zero", "minutes") is None


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize(
    "text,tool",
    [
        ("open youtube", "open_website"),
        ("open notepad", "open_app"),
        ("open example.com", "open_website"),
        ("launch calculator", "open_app"),
        ("play lo-fi beats on youtube", "play_youtube"),
        ("search for cats on youtube", "open_website"),
        ("take a screenshot", "take_screenshot"),
        ("volume up", "volume"),
        ("set volume to 55", "volume"),
        ("mute", "volume"),
        ("pause", "media_control"),
        ("next song", "media_control"),
        ("what time is it", "time"),
        ("what's the weather in pune", "weather"),
        ("news about ai", "news"),
        ("remind me in 10 minutes to stretch", "set_reminder"),
        ("set a timer for 5 minutes", "set_timer"),
        ("make a ppt about solar power", "create_presentation"),
        ("write a python script that sorts files", "write_code"),
        ("lock my pc", "lock_screen"),
        ("shutdown my computer", "shutdown_pc"),
        ("calculate 12 * 9", "calculator"),
        ("what is 25 multiplied by 47", "calculator"),
        ("wiki alan turing", "wikipedia"),
        ("who is marie curie", "wikipedia"),
        ("search the web for rust tutorials", "web_search"),
        ("copy hello to the clipboard", "clipboard_write"),
        ("what's in my clipboard", "clipboard_read"),
        ("list files", "list_directory"),
        ("convert 5 km to miles", "unit_converter"),
        ("type hello world", "type_text"),
        ("press ctrl+s", "press_keys"),
        ("what operating system am i running", "system_info"),
        ("what's my ip", "network_info"),
        ("say good morning", "speak"),
        ("note down buy milk", "quick_note"),
    ],
)
def test_intent_routing(engine: IntentEngine, text: str, tool: str):
    match = engine.match(text)
    assert match is not None, f"No match for {text!r}"
    assert match.tool_name == tool


@pytest.mark.parametrize(
    "text",
    [
        "tell me a story about dragons",
        "how are you today",
        "explain quantum entanglement simply",
        "what do you think about the future of AI",
        "",
    ],
)
def test_conversational_falls_through(engine: IntentEngine, text: str):
    assert engine.match(text) is None


# ------------------------------------------------------------------- slots
def test_slot_extraction_reminder(engine: IntentEngine):
    m = engine.match("remind me in 10 minutes to stretch")
    assert m.arguments == {"text": "stretch", "in_seconds": 600}

    m = engine.match("remind me to call mom at 5 pm")
    assert m.arguments == {"text": "call mom", "at_time": "17:00"}


def test_slot_extraction_site_query(engine: IntentEngine):
    m = engine.match("search for mechanical keyboards on amazon")
    assert m.arguments == {"site": "amazon", "query": "mechanical keyboards"}


def test_slot_extraction_volume(engine: IntentEngine):
    m = engine.match("set volume to 300")
    assert m.arguments == {"action": "set", "level": 100}  # clamped


def test_hindi_math(engine: IntentEngine):
    m = engine.match("25 को 40 से गुणा करो")
    assert m.tool_name == "calculator"
    assert m.arguments["expression"] == "25 * 40"


def test_content_intents_need_generation(engine: IntentEngine):
    m = engine.match("make a ppt about renewable energy")
    assert m.needs_generation is True
    assert m.arguments["topic"] == "renewable energy"


def test_dynamic_open_resolution(engine: IntentEngine):
    site = engine.match("open gmail")
    assert site.tool_name == "open_website"
    app = engine.match("open task manager")
    assert app.tool_name == "open_app"


def test_file_search_beats_web_search(engine: IntentEngine):
    assert engine.match("search files named report").tool_name == "search_files"
    assert engine.match("search for files called notes").tool_name == "search_files"
    assert engine.match("search for rust tutorials").tool_name == "web_search"


def test_open_folder_names(engine: IntentEngine):
    m = engine.match("open downloads")
    assert m.tool_name == "open_path"
    assert m.arguments["path"] == "~/Downloads"
