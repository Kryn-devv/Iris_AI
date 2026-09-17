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
        monkeypatch.setattr(type(kernel.model_gateway), "can_answer", property(lambda self: True))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", False)
        res = await kernel.process_request("how are you")
        assert res.handler != "smalltalk", res.response

    async def test_with_no_model_at_all_she_still_answers(self, monkeypatch):
        """Zero keys, no network: answering 'how are you' is a promise kept."""
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "can_answer", property(lambda self: False))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", False)
        res = await kernel.process_request("how are you")
        assert res.handler == "smalltalk" and res.response.strip()

    async def test_a_key_that_is_rate_limited_still_gets_a_warm_reply(self, monkeypatch):
        """The normal state of a free tier on a busy afternoon. Falling through
        to the offline engine here answers "how are you" with the blurb telling
        you to add an API key you already have."""
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "has_cloud", property(lambda self: True))
        monkeypatch.setattr(type(kernel.model_gateway), "can_answer", property(lambda self: False))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", False)
        res = await kernel.process_request("how are you")
        assert res.handler == "smalltalk", res.response
        assert "API key" not in res.response

    async def test_the_setting_forces_it_on(self, monkeypatch):
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "can_answer", property(lambda self: True))
        monkeypatch.setattr("iris.app.core.config.settings.SMALLTALK_ENABLED", True)
        res = await kernel.process_request("how are you")
        assert res.handler == "smalltalk"

    async def test_a_smalltalk_turn_is_remembered(self, monkeypatch):
        """She has to know the exchange happened, or she contradicts it next turn."""
        kernel = make_kernel()
        monkeypatch.setattr(type(kernel.model_gateway), "can_answer", property(lambda self: False))
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


class TestTheFillerWhileSheWorks:
    """Silence while something slow runs is the other way an assistant feels
    dead. A person says "one sec"."""

    @staticmethod
    def _slow_tool(seconds: float, network: bool = True):
        class Slow(BaseTool):
            name = "slow_demo"
            description = "Takes a while."
            permission_level = PermissionLevel.READ
            category = ToolCategory.CORE

            async def _run(self, **_):
                import asyncio as _a
                await _a.sleep(seconds)
                return {"speech": "Here it is."}

        Slow.network = network
        return Slow()

    async def _run_with(self, monkeypatch, tool, delay_ms=20):
        import asyncio

        spoken = []

        class FakeVoice:
            async def speak(self, text, **kw):
                spoken.append((text, kw.get("filler", False)))

        monkeypatch.setattr("iris.app.voice.service.default_voice_service", FakeVoice())
        monkeypatch.setattr("iris.app.core.config.settings.THINKING_FILLER_MS", delay_ms)

        registry = ToolRegistry()
        registry.register(tool, quiet=True)
        kernel = AgentKernel(
            tool_registry=registry,
            permission_manager=PermissionManager(),
            intent_engine=IntentEngine([
                Rule(name="slow", intent="test", tool="slow_demo", pattern=_rx(r"^slow$")),
            ]),
        )
        res = await kernel.process_request("slow", conversation_id="c1")
        return res, spoken

    async def test_a_slow_network_command_gets_a_stop_gap(self, monkeypatch):
        res, spoken = await self._run_with(monkeypatch, self._slow_tool(0.25))
        assert res.status == "COMPLETED" and "here it is" in res.response.lower()
        assert spoken and spoken[0][1] is True, spoken
        assert len(spoken[0][0]) < 40                      # short on purpose

    async def test_a_fast_command_is_never_talked_over(self, monkeypatch):
        """The answer beats the timer, so the stop-gap must not be said at all."""
        res, spoken = await self._run_with(monkeypatch, self._slow_tool(0.0), delay_ms=400)
        assert res.status == "COMPLETED"
        assert spoken == []

    async def test_a_local_command_never_gets_one(self, monkeypatch):
        """Local tools answer immediately; only the network is worth waiting on."""
        res, spoken = await self._run_with(monkeypatch, self._slow_tool(0.25, network=False))
        assert res.status == "COMPLETED" and spoken == []

    async def test_setting_it_to_zero_turns_it_off(self, monkeypatch):
        res, spoken = await self._run_with(monkeypatch, self._slow_tool(0.2), delay_ms=0)
        assert res.status == "COMPLETED" and spoken == []

    async def test_a_broken_voice_never_breaks_the_command(self, monkeypatch):
        class Broken:
            async def speak(self, *a, **k):
                raise RuntimeError("no audio device")

        monkeypatch.setattr("iris.app.voice.service.default_voice_service", Broken())
        monkeypatch.setattr("iris.app.core.config.settings.THINKING_FILLER_MS", 20)
        registry = ToolRegistry()
        registry.register(self._slow_tool(0.2), quiet=True)
        kernel = AgentKernel(
            tool_registry=registry,
            permission_manager=PermissionManager(),
            intent_engine=IntentEngine([
                Rule(name="slow", intent="test", tool="slow_demo", pattern=_rx(r"^slow$")),
            ]),
        )
        res = await kernel.process_request("slow", conversation_id="c1")
        assert res.status == "COMPLETED" and "here it is" in res.response.lower()


class TestApprovedCommands:
    """Approving a risky action is still a thing she did."""

    class RiskyTool(BaseTool):
        name = "risky_demo"
        description = "Needs a yes first."
        permission_level = PermissionLevel.CONFIRM_REQUIRED
        category = ToolCategory.CORE

        async def _run(self, **_):
            return {"speech": "Deleted the folder."}

    def _kernel(self):
        registry = ToolRegistry()
        registry.register(self.RiskyTool(), quiet=True)
        return AgentKernel(
            tool_registry=registry,
            permission_manager=PermissionManager(),
            intent_engine=IntentEngine([
                Rule(name="risky", intent="test", tool="risky_demo",
                     pattern=_rx(r"^do the risky thing$")),
            ]),
        )

    async def test_the_reply_is_in_her_voice_too(self):
        """It used to come back flat, because the confirmation path had its own
        exit that skipped everything the ordinary path does."""
        said = set()
        for _ in range(10):
            kernel = self._kernel()
            first = await kernel.process_request("do the risky thing", conversation_id="c1")
            assert first.pending_action, first.response
            done = await kernel.resume_task_confirmation(first.task_id, approved=True)
            assert "deleted the folder" in done.response.lower(), done.response
            said.add(done.response)
        assert len(said) >= 3, said

    async def test_and_it_is_remembered(self):
        kernel = self._kernel()
        first = await kernel.process_request("do the risky thing", conversation_id="c1")
        await kernel.resume_task_confirmation(first.task_id, approved=True)
        history = await kernel.conversation_memory.retrieve("c1")
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert "deleted the folder" in history[1]["content"].lower()

    async def test_saying_no_still_cancels_plainly(self):
        kernel = self._kernel()
        first = await kernel.process_request("do the risky thing", conversation_id="c1")
        done = await kernel.resume_task_confirmation(first.task_id, approved=False)
        assert "won't run" in done.response
