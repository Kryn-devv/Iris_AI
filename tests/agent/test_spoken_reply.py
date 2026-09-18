"""What she says out loud, as distinct from what she writes.

Three complaints from one session with a real user, all of them the same
underlying gap — nothing ever decided what a *spoken* reply should be:

* "sometimes it doesn't speak" — the UI spoke a reply only when it was under
  300 characters, so every answer with substance in it was silent;
* "2 voices at same time" — the "one sec" filler played on the machine's
  speakers while the browser read the answer aloud, and neither could stop
  the other;
* "it's just pasting walls of text" — the same string was the thing on screen
  and the thing in her mouth, so a code block got read out symbol by symbol.
"""

from __future__ import annotations

import pytest

from iris.app.agent.persona import SPOKEN_LEAD_CHARS, spoken_lead


class TestSpokenLead:
    def test_a_short_answer_is_spoken_whole(self):
        assert spoken_lead("Opened YouTube.") == "Opened YouTube."

    def test_code_is_shown_and_not_narrated(self):
        reply = (
            "All set — the Flask app serves Hello Prajjwal on port 5000.\n\n"
            "```python\nfrom flask import Flask\napp = Flask(__name__)\n```\n\n"
            "Run it with python hello_server.py."
        )
        said = spoken_lead(reply)
        assert "Flask app serves Hello Prajjwal" in said
        assert "from flask import" not in said
        assert "```" not in said

    def test_an_unterminated_code_block_is_still_not_read_out(self):
        """A reply cut off by a token limit leaves its fence open."""
        said = spoken_lead("Here you go.\n\n```python\nimport os\nos.listdir()")
        assert said == "Here you go."

    def test_a_wall_of_text_is_cut_at_a_sentence(self):
        reply = " ".join(f"Sentence number {i} says something." for i in range(40))
        said = spoken_lead(reply)
        assert len(said) <= SPOKEN_LEAD_CHARS
        assert said.endswith("."), said
        assert reply.startswith(said), "a lead is her own words, never a paraphrase"

    def test_one_enormous_sentence_is_cut_at_a_word(self):
        reply = "so " * 400
        said = spoken_lead(reply)
        assert len(said) <= SPOKEN_LEAD_CHARS + 1
        assert "  " not in said

    def test_markdown_punctuation_does_not_reach_the_ear(self):
        said = spoken_lead("# Heading\n\n- **first** point\n- second point")
        for noise in ("#", "*", "- "):
            assert noise not in said
        assert "first point" in said

    def test_a_link_is_spoken_by_its_label(self):
        assert spoken_lead("See [the docs](https://example.com/x) for that.") == (
            "See the docs for that."
        )

    def test_a_reply_that_is_only_code_says_nothing(self):
        """Silence is right here: a person pastes the snippet, they don't read it."""
        assert spoken_lead("```python\nprint('hi')\n```") == ""

    def test_devanagari_sentences_split_on_the_danda(self):
        reply = "यह पहला वाक्य है। " * 30
        said = spoken_lead(reply)
        assert len(said) <= SPOKEN_LEAD_CHARS
        assert said.endswith("।")

    @pytest.mark.parametrize("value", ["", None])
    def test_nothing_in_nothing_out(self, value):
        assert spoken_lead(value) == ""


class TestEveryReplyIsSayable:
    """The UI can only speak what the server gives it."""

    async def test_a_long_answer_arrives_with_something_to_say(self):
        from tests.agent.test_human_mode import make_kernel

        kernel = make_kernel()
        state = kernel.task_manager.create_task("explain mitosis")
        long_answer = " ".join(f"Point {i} about mitosis." for i in range(60))
        res = kernel._response(state, long_answer)

        assert res.speech, "a long reply used to be sent with speech=None, and was silent"
        assert len(res.speech) <= SPOKEN_LEAD_CHARS
        assert long_answer.startswith(res.speech)

    async def test_a_tools_own_sentence_still_wins(self):
        """A tool that said how to say it is never second-guessed."""
        from tests.agent.test_human_mode import make_kernel

        kernel = make_kernel()
        state = kernel.task_manager.create_task("temp")
        res = kernel._response(state, "Temperature: 24.5 C\nHumidity: 60%",
                               speech="It's 24 and a half degrees.")
        assert res.speech == "It's 24 and a half degrees."


class TestOneMouthAtATime:
    """Two voices at once was a filler on the speakers over a browser reply."""

    async def test_a_web_turns_filler_goes_to_the_browser(self, monkeypatch):
        from tests.agent.test_human_mode import make_kernel

        calls = {}

        async def fake_speak(text, **kwargs):
            calls.update(kwargs)
            calls["text"] = text
            return {"spoken": False}

        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.speak", fake_speak
        )
        kernel = make_kernel()
        state = kernel.task_manager.create_task("x")
        state.metadata["channel"] = "web"
        await kernel._say_filler(0.0, state)

        assert calls["browser_only"] is True, "otherwise it plays over the answer"
        assert calls["filler"] is True

    async def test_a_robots_own_microphone_still_uses_the_speakers(self, monkeypatch):
        """No browser in that loop — the speakers are the only mouth there is."""
        from tests.agent.test_human_mode import make_kernel

        calls = {}

        async def fake_speak(text, **kwargs):
            calls.update(kwargs)
            return {"spoken": True}

        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.speak", fake_speak
        )
        kernel = make_kernel()
        state = kernel.task_manager.create_task("x")
        state.metadata["channel"] = "node"
        await kernel._say_filler(0.0, state)

        assert calls["browser_only"] is False


class TestVoiceServiceBrowserOnly:
    async def test_browser_only_never_touches_the_server_engine(self, monkeypatch):
        from iris.app.core.bus import Topics, default_event_bus
        from iris.app.voice.service import VoiceService

        service = VoiceService()
        monkeypatch.setattr(
            service, "_get_tts",
            lambda: (_ for _ in ()).throw(AssertionError("server engine must not be probed")),
        )
        seen = []
        unsub = default_event_bus.subscribe_sync(Topics.VOICE_SPEAKING, seen.append) \
            if hasattr(default_event_bus, "subscribe_sync") else None

        result = await service.speak("one sec", browser_only=True)

        assert result["engine"] == "browser"
        assert result["spoken"] is False
        if unsub:
            unsub()


class TestQuestionsReachTheModel:
    """"who is narendra modi" came back as an encyclopedia's first sentence.

    Accurate, and nothing like an answer from someone you are talking to. That
    one exchange is what "it's just if/else" meant.
    """

    @staticmethod
    def _kernel_with_who_is(monkeypatch, can_answer):
        from iris.app.agent.kernel import AgentKernel
        from iris.app.core.security import PermissionManager
        from iris.app.nlu.engine import IntentEngine
        from iris.app.nlu.rules import RULES
        from iris.app.tools.registry import ToolRegistry

        who_is = [r for r in RULES if r.name == "who_is"]
        assert who_is, "the rule this test is about has been renamed"
        kernel = AgentKernel(
            tool_registry=ToolRegistry(),
            permission_manager=PermissionManager(),
            intent_engine=IntentEngine(who_is),
        )
        monkeypatch.setattr(
            type(kernel.model_gateway), "can_answer", property(lambda self: can_answer)
        )
        return kernel

    def test_with_a_model_the_lookup_steps_aside(self, monkeypatch):
        kernel = self._kernel_with_who_is(monkeypatch, can_answer=True)
        match = kernel.intent_engine.match("who is narendra modi")
        assert match is not None and match.prefer_model
        assert kernel._prefers_model(match) is True

    def test_with_no_model_the_lookup_is_still_far_better_than_nothing(self, monkeypatch):
        """Offline, a Wikipedia answer beats the offline engine's shrug."""
        kernel = self._kernel_with_who_is(monkeypatch, can_answer=False)
        match = kernel.intent_engine.match("who is narendra modi")
        assert kernel._prefers_model(match) is False

    def test_a_real_command_is_never_deferred(self, monkeypatch):
        from iris.app.nlu.engine import IntentEngine
        from iris.app.nlu.rules import RULES

        kernel = self._kernel_with_who_is(monkeypatch, can_answer=True)
        kernel.intent_engine = IntentEngine(RULES)
        for command in ("open youtube", "take a screenshot", "what time is it"):
            match = kernel.intent_engine.match(command)
            assert match is not None, command
            assert kernel._prefers_model(match) is False, command

    def test_a_gateway_that_cannot_say_keeps_the_deterministic_path(self, monkeypatch):
        kernel = self._kernel_with_who_is(monkeypatch, can_answer=True)

        def boom(self):
            raise RuntimeError("gateway is rebuilding")

        monkeypatch.setattr(type(kernel.model_gateway), "can_answer", property(boom))
        match = kernel.intent_engine.match("who is ada lovelace")
        assert kernel._prefers_model(match) is False


class TestVoiceOnlyChannelsKeepTheWholeAnswer:
    """A lead is only honest when a screen is carrying the rest of it."""

    async def test_the_robots_microphone_is_not_cut_to_two_sentences(self):
        from tests.agent.test_human_mode import make_kernel

        kernel = make_kernel()
        state = kernel.task_manager.create_task("explain it")
        state.metadata["channel"] = "voice"
        long_answer = " ".join(f"Point {i} about the thing." for i in range(60))
        res = kernel._response(state, long_answer)

        assert res.speech is None, (
            "with speech unset the node route speaks the full reply, trimmed at "
            "SPEECH_MAX_CHARS — a 260-character lead would simply lose the answer"
        )

    async def test_a_tools_sentence_still_reaches_a_voice_only_channel(self):
        from tests.agent.test_human_mode import make_kernel

        kernel = make_kernel()
        state = kernel.task_manager.create_task("temp")
        state.metadata["channel"] = "voice"
        res = kernel._response(state, "Temp: 24.5C", speech="It's 24 and a half.")
        assert res.speech == "It's 24 and a half."

    async def test_the_browser_still_gets_its_lead(self):
        from tests.agent.test_human_mode import make_kernel

        kernel = make_kernel()
        state = kernel.task_manager.create_task("explain it")
        state.metadata["channel"] = "web"
        res = kernel._response(state, " ".join(f"Point {i} here." for i in range(60)))
        assert res.speech and len(res.speech) <= SPOKEN_LEAD_CHARS
