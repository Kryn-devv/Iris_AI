"""The live proof that a command means the same thing every time."""

import pytest

from iris.app.nlu.engine import IntentEngine
from iris.app.tools.builtin.reliability import ReliabilityCheckTool


async def test_a_command_is_deterministic_a_hundred_times():
    res = await ReliabilityCheckTool().execute(phrase="take a U-turn", times=100)
    assert res.success, res.error
    assert res.result["distinct_decisions"] == 1
    assert res.result["deterministic"] is True and res.result["model_involved"] is False
    assert res.result["decision"] == {"tool": "robot_navigate", "arguments": {"preset": "u_turn"}}
    assert "100 out of 100 came out identical" in res.result["speech"]
    assert "No AI model was involved" in res.result["speech"]


async def test_the_default_phrase_and_times():
    res = await ReliabilityCheckTool().execute()
    assert res.result["times"] == 100 and res.result["phrase"] == "take a U-turn"


async def test_a_chat_question_is_named_as_the_models_job():
    res = await ReliabilityCheckTool().execute(phrase="what is the meaning of life", times=5)
    assert res.success
    assert res.result["model_involved"] is True and res.result["deterministic"] is False
    assert "conversation model" in res.result["speech"]


async def test_times_are_clamped():
    res = await ReliabilityCheckTool().execute(phrase="robot forward", times=5000)
    assert res.result["times"] == 1000


@pytest.mark.parametrize("text,phrase", [
    ("are you reliable", None), ("are you reliable?", None), ("prove you are reliable", None),
    ("show me that you're reliable", None), ("how reliable are you", None),
    ("reliability test", None), ("run a reliability test on go back to the board", "go back to the board"),
    ("consistency check for take a u-turn", "take a u-turn"),
])
def test_phrases_reach_the_tool(text, phrase):
    match = IntentEngine().match(text)
    assert match is not None and match.tool_name == "reliability_check", (text, match)
    assert match.arguments.get("phrase") == phrase
