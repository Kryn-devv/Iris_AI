"""Human mode end to end: does the whole pipeline sound like a person?

Three habits made Iris feel like a vending machine, and each one is a test
here: canned pleasantries the model never saw, identical confirmations for
every command, and a transcript that either forgot the conversation or carried
all of it until the rate limit bit.
"""

from __future__ import annotations

import pytest

from iris.app.agent.kernel import AgentKernel
from iris.app.agent.rapport import RapportTracker, default_rapport
from iris.app.core.security import PermissionLevel, PermissionManager
from iris.app.memory.conversation import ConversationMemory
from iris.app.nlu.engine import IntentEngine
from iris.app.nlu.rules import Rule, _rx
from iris.app.schemas.tools import ToolCategory, ToolParameterSchema
from iris.app.tools.base import BaseTool
from iris.app.tools.registry import ToolRegistry


class OpenTool(BaseTool):
    name = "open_demo"
    description = "Open something."
    permission_level = PermissionLevel.READ
    category = ToolCategory.CORE
    input_schema = ToolParameterSchema(properties={"what": {"type": "string"}})

    async def _run(self, what: str = "YouTube", **_):
        return {"speech": f"Opened {what}.", "display": f"Opened {what}."}


class PlainTool(BaseTool):
    """A tool that reports nothing but success — the bare-ack case."""

    name = "plain_demo"
    description = "Do a thing."
    permission_level = PermissionLevel.READ
    category = ToolCategory.CORE

    async def _run(self, **_):
        return {"speech": "Done."}


def make_kernel(**kwargs) -> AgentKernel:
    """A kernel with two tools and its own fresh memory."""
    registry = ToolRegistry()
    registry.register(OpenTool(), quiet=True)
    registry.register(PlainTool(), quiet=True)
    rules = [
        Rule(name="open", intent="test", tool="open_demo", pattern=_rx(r"^open it$")),
        Rule(name="plain", intent="test", tool="plain_demo", pattern=_rx(r"^do the thing$")),
    ]
    return AgentKernel(
        tool_registry=registry,
        permission_manager=PermissionManager(),
        intent_engine=IntentEngine(rules),
        **kwargs,
    )


def fake_state(conversation_id=None, user_input="hi"):
    """The handful of attributes the context assembler actually reads."""
    return type("S", (), {
        "conversation_id": conversation_id, "user_input": user_input,
        "metadata": {}, "steps": [], "task_id": "t",
    })()


@pytest.fixture(autouse=True)
def fresh_rapport():
    default_rapport.clear()
    yield
    default_rapport.clear()


class TestSmalltalkGate:
    """A canned reply the model never sees is how a thread gets dropped."""

    async def test_with_a_model_available_conversation_reaches_it(self, monkeypatch):
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "has_cloud", property(lambda self: True))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", False)
        res = await kernel.process_request("how are you")
        assert res.handler != "smalltalk", res.response

    async def test_with_no_model_at_all_she_still_answers(self, monkeypatch):
        """Zero keys, no network: answering 'how are you' is a promise kept."""
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "has_cloud", property(lambda self: False))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", False)
        res = await kernel.process_request("how are you")
        assert res.handler == "smalltalk" and res.response.strip()

    async def test_the_setting_forces_it_on(self, monkeypatch):
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "has_cloud", property(lambda self: True))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", True)
        res = await kernel.process_request("how are you")
        assert res.handler == "smalltalk"

    async def test_a_smalltalk_turn_is_remembered(self, monkeypatch):
        """She has to know the exchange happened, or she contradicts it next turn."""
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "has_cloud", property(lambda self: False))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", True)
        await kernel.process_request("how are you", conversation_id="c1")
        history = await kernel.conversation_memory.retrieve("c1")
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert history[0]["content"] == "how are you"


class TestConfirmationsSoundAlive:
    async def test_the_same_command_is_not_answered_identically(self):
        kernel = make_kernel()
        said = set()
        for _ in range(12):
            res = await kernel.process_request("do the thing", conversation_id="c1")
            said.add(res.response)
        assert len(said) >= 4, said

    async def test_but_the_tool_s_words_always_survive(self):
        kernel = make_kernel()
        for _ in range(12):
            res = await kernel.process_request("open it", conversation_id="c1")
            # A lead-in lower-cases the first word ("Hey — opened YouTube."),
            # which is how people talk; the words themselves are all still there.
            assert "opened youtube" in res.response.lower(), res.response
            assert "YouTube" in res.response, res.response

    async def test_turning_it_off_restores_the_flat_ack(self, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.PERSONA_ACKS", False)
        kernel = make_kernel()
        res = await kernel.process_request("do the thing")
        assert res.response == "Done."

    async def test_the_spoken_and_written_lines_agree(self):
        """One sentence shown and a different one spoken is unsettling."""
        kernel = make_kernel()
        for _ in range(8):
            res = await kernel.process_request("open it", conversation_id="c1")
            assert res.speech == res.response, (res.speech, res.response)

    async def test_a_burst_of_commands_gets_shorter_answers(self, monkeypatch):
        """Firing commands fast, a person stops decorating. So does she."""
        clock = type("C", (), {"t": 0.0, "__call__": lambda self: self.t})()
        tracker = RapportTracker(clock=clock, wall_clock=lambda: 14)
        monkeypatch.setattr("iris.app.agent.kernel.default_rapport", tracker)
        monkeypatch.setattr("iris.app.agent.rapport.default_rapport", tracker)
        kernel = make_kernel()
        lengths = []
        for _ in range(5):
            res = await kernel.process_request("open it", conversation_id="c1")
            lengths.append(len(res.response))
            clock.t += 3
        assert lengths[-1] <= lengths[0], lengths
        assert res.response == "Opened YouTube."


class TestTheThreadIsKept:
    async def test_history_is_capped_so_a_long_chat_survives(self, monkeypatch):
        """Unbounded history is what tripped the free tier mid-conversation."""
        monkeypatch.setattr("iris.app.core.config.settings.HISTORY_MAX_TURNS", 3)
        kernel = make_kernel()
        for i in range(10):
            await kernel.process_request("open it", conversation_id="c1")

        context = await kernel.context_assembler.assemble_context(
fake_state("c1", "and now?")
        )
        history = [m for m in context["messages"] if m["role"] in ("user", "assistant")]
        assert len(history) <= 2 * 3 + 1, len(history)

    async def test_the_window_never_opens_on_an_orphaned_reply(self, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.HISTORY_MAX_TURNS", 2)
        kernel = make_kernel()
        await kernel.conversation_memory.remember(
            "c1", [{"role": "user", "content": f"u{i}"} if i % 2 == 0
                   else {"role": "assistant", "content": f"a{i}"} for i in range(20)])
        context = await kernel.context_assembler.assemble_context(
fake_state("c1", "next")
        )
        first = next(m for m in context["messages"] if m["role"] != "system")
        assert first["role"] == "user"

    async def test_what_happened_reaches_the_prompt(self, monkeypatch):
        clock = type("C", (), {"t": 0.0, "__call__": lambda self: self.t})()
        tracker = RapportTracker(clock=clock, wall_clock=lambda: 14)
        monkeypatch.setattr("iris.app.agent.kernel.default_rapport", tracker)
        monkeypatch.setattr("iris.app.agent.rapport.default_rapport", tracker)

        kernel = make_kernel()
        await kernel.process_request("open it", conversation_id="c1")
        clock.t += 90 * 60           # an hour and a half away

        context = await kernel.context_assembler.assemble_context(
fake_state("c1", "back")
        )
        system = context["messages"][0]["content"]
        assert "CONVERSATION SO FAR" in system and "been away" in system

    async def test_the_character_brief_is_the_system_prompt(self):
        kernel = make_kernel()
        context = await kernel.context_assembler.assemble_context(
fake_state()
        )
        system = context["messages"][0]["content"]
        assert system.startswith("You are") and "vending" not in system
        assert "no markdown" in system.lower()


class TestNothingHereCanBreakAReply:
    async def test_a_broken_tracker_is_survivable(self, monkeypatch):
        class Exploding:
            def moment(self, *a, **k): raise RuntimeError("boom")
            def repeats(self, *a, **k): raise RuntimeError("boom")
            def note_turn(self, *a, **k): raise RuntimeError("boom")
            def maybe_summarize(self, *a, **k): raise RuntimeError("boom")
            def context_block(self, *a, **k): raise RuntimeError("boom")

        monkeypatch.setattr("iris.app.agent.kernel.default_rapport", Exploding())
        monkeypatch.setattr("iris.app.agent.rapport.default_rapport", Exploding())
        kernel = make_kernel()
        # The ack path reads the tracker, so a broken one must not take the
        # turn down with it — worst case she sounds flat.
        res = await kernel.process_request("open it", conversation_id="c1")
        assert res.status == "COMPLETED"
        assert "opened youtube" in res.response.lower(), res.response
