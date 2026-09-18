"""Can she reach the tools the request needs?

One session answered "can you do it on my Linux" with "I can't run the
commands on your machine directly". IRIS has ``run_command`` and
``run_python``, both registered and both working — but the agent loop is
handed a keyword-matched subset of the catalogue, and those two never
matched. The reply was false about IRIS and true about the list the model had
been given.

The same gap produced the worse failure in that session: asked to demonstrate
everything, with no tool available that could, the model wrote a transcript of
itself using tools instead — a timer never started, a reminder never set, and
a twelve-core Linux box reported to someone on a four-core MacBook Air.
"""

from __future__ import annotations

import pytest

from iris.app.agent.kernel import AgentKernel
from iris.app.tools.loader import load_all_tools
from iris.app.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def kernel() -> AgentKernel:
    registry = ToolRegistry()
    load_all_tools(registry, quiet=True)
    return AgentKernel(tool_registry=registry)


def names_for(kernel: AgentKernel, message: str) -> set:
    return {tool.name for tool in kernel._select_tools_for(message)}


#: Real messages from the session, including the ones that failed.
REAL_MESSAGES = [
    "can you do it on my Linux",
    "can you do something for me",
    "everything one by one everything",
    "just make me test yourself like do all the functions",
    "I want to know is my storage SSD or HDD and how much space is left on it",
    "yo",
    "kal ka plan kya hai",
    "open youtube",
    "what is mitosis",
    "fix this for me",
    "check it",
]


class TestSheCanAlwaysAct:
    @pytest.mark.parametrize("message", REAL_MESSAGES)
    def test_running_something_is_never_off_the_table(self, kernel, message):
        """The capability that makes her an assistant rather than a search box."""
        selected = names_for(kernel, message)
        assert "run_command" in selected, message
        assert "run_python" in selected, message

    @pytest.mark.parametrize("message", REAL_MESSAGES)
    def test_she_always_knows_her_own_machine(self, kernel, message):
        assert "system_info" in names_for(kernel, message), message

    @pytest.mark.parametrize("message", REAL_MESSAGES)
    def test_the_ceiling_is_respected(self, kernel, message):
        assert len(names_for(kernel, message)) <= kernel._MAX_AGENT_TOOLS, message

    def test_asking_what_she_can_do_reaches_the_tool_that_knows(self, kernel):
        for phrasing in ("what can you do", "show me everything you can do",
                         "list your tools", "demo all your functions"):
            assert "capabilities" in names_for(kernel, phrasing), phrasing


class TestTheRightToolsWinTheRoom:
    """With a hard ceiling, arbitrary order lets the ceiling pick the skills."""

    def test_a_named_tool_beats_one_that_merely_mentions_the_word(self, kernel):
        assert "weather" in names_for(kernel, "whats the weather in bangalore")

    def test_the_same_question_always_gets_the_same_tools(self, kernel):
        """Abilities that shuffle between identical requests are not dependable."""
        first = [t.name for t in kernel._select_tools_for("open youtube and search for cats")]
        for _ in range(5):
            assert [t.name for t in kernel._select_tools_for(
                "open youtube and search for cats")] == first

    def test_a_screenshot_request_can_take_a_screenshot(self, kernel):
        assert "take_screenshot" in names_for(kernel, "take a screenshot of my screen")

    def test_nonsense_still_returns_a_usable_kit(self, kernel):
        selected = names_for(kernel, "asdkjh qwe zxc")
        assert selected >= {"run_command", "system_info", "web_search"}


class TestSchemaBudget:
    def test_the_kit_stays_inside_a_free_tiers_minute(self, kernel):
        """Groq's free tier meters ~8k tokens a minute; schemas are not free."""
        import json

        from iris.app.agent.context import ToolSchemaAdapter

        for message in REAL_MESSAGES:
            schemas = [ToolSchemaAdapter.from_tool(t)
                       for t in kernel._select_tools_for(message)]
            approx_tokens = len(json.dumps(schemas)) // 4
            assert approx_tokens < 4000, (message, approx_tokens)
