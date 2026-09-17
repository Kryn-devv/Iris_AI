"""Knowing the shape of the conversation: gaps, bursts, and the long memory.

None of this needs intelligence — a clock and a short memory are enough — but
it is the difference between an assistant that is present and one that answers.
"""

from __future__ import annotations

import asyncio

import pytest

from iris.app.agent.rapport import RapportTracker, _duration, _transcript


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def rapport(clock):
    return RapportTracker(clock=clock, wall_clock=lambda: 14)


CID = "conv-1"


class TestSituation:
    def test_first_contact_is_not_a_reunion(self, rapport):
        """"Welcome back" to someone who has said nothing yet is wrong."""
        moment = rapport.moment(CID)
        assert moment.returning is False and moment.turns == 0

    def test_but_a_real_absence_is(self, rapport, clock):
        rapport.note_turn(CID)
        clock.tick(45 * 60)
        assert rapport.moment(CID).returning is True

    def test_straight_after_a_turn_it_is_not(self, rapport):
        rapport.note_turn(CID)
        moment = rapport.moment(CID)
        assert moment.returning is False and moment.turns == 1

    def test_an_hour_of_silence_makes_the_next_reply_a_greeting(self, rapport, clock):
        rapport.note_turn(CID)
        clock.tick(60 * 60)
        moment = rapport.moment(CID)
        assert moment.returning is True
        assert moment.gap_minutes == pytest.approx(60, abs=0.1)

    def test_a_short_pause_does_not(self, rapport, clock):
        rapport.note_turn(CID)
        clock.tick(120)
        assert rapport.moment(CID).returning is False

    def test_commands_fired_fast_are_a_burst(self, rapport, clock):
        for _ in range(3):
            rapport.note_turn(CID, tool_name="open_app", was_command=True)
            clock.tick(5)
        assert rapport.moment(CID).terse is True

    def test_commands_spread_out_are_not(self, rapport, clock):
        for _ in range(3):
            rapport.note_turn(CID, tool_name="open_app", was_command=True)
            clock.tick(120)
        assert rapport.moment(CID).terse is False

    def test_conversation_is_never_a_burst(self, rapport, clock):
        for _ in range(4):
            rapport.note_turn(CID, was_command=False)
            clock.tick(3)
        assert rapport.moment(CID).terse is False

    def test_coming_back_outranks_a_stale_burst(self, rapport, clock):
        for _ in range(3):
            rapport.note_turn(CID, was_command=True)
            clock.tick(2)
        clock.tick(60 * 60)
        moment = rapport.moment(CID)
        assert moment.returning is True and moment.terse is False

    def test_asking_for_the_same_thing_twice(self, rapport):
        rapport.note_turn(CID, tool_name="open_app")
        assert rapport.repeats(CID, "open_app") is True
        assert rapport.repeats(CID, "take_screenshot") is False

    def test_repeats_on_an_unknown_conversation_is_false(self, rapport):
        assert rapport.repeats("nobody", "open_app") is False
        assert rapport.repeats(None, None) is False

    def test_a_turn_with_no_conversation_is_not_tracked(self, rapport):
        """Scheduled commands and the robot's own microphone arrive without an
        id. Bucketing them together made a background timer look like the
        person asking for the same thing twice."""
        rapport.note_turn(None, tool_name="time", was_command=True)
        rapport.note_turn(None, tool_name="time", was_command=True)
        assert rapport._threads == {}
        assert rapport.moment(None).turns == 0
        assert rapport.repeats(None, "time") is False
        assert rapport.context_block(None) == ""

    def test_an_id_less_turn_cannot_disturb_a_real_conversation(self, rapport, clock):
        rapport.note_turn(CID, tool_name="weather", was_command=True)
        clock.tick(60 * 60)
        for _ in range(4):                       # a cron job firing meanwhile
            rapport.note_turn(None, tool_name="time", was_command=True)
        moment = rapport.moment(CID)
        assert moment.returning is True          # the hour away still registers
        assert moment.terse is False             # and they did not earn a burst

    def test_session_length_is_tracked(self, rapport, clock):
        rapport.note_turn(CID)
        clock.tick(3 * 60 * 60)
        assert rapport.moment(CID).session_minutes == pytest.approx(180, abs=1)

    def test_old_conversations_fall_off(self, rapport):
        from iris.app.agent.rapport import MAX_CONVERSATIONS

        for i in range(MAX_CONVERSATIONS + 10):
            rapport.note_turn(f"c{i}")
        assert len(rapport._threads) == MAX_CONVERSATIONS
        assert rapport.moment("c0").turns == 0          # evicted, treated as new


class TestContextBlock:
    def test_nothing_to_say_about_a_new_conversation(self, rapport):
        assert rapport.context_block("unknown") == ""

    def test_it_mentions_the_gap_and_the_tools(self, rapport, clock):
        rapport.note_turn(CID, tool_name="camera_who")
        clock.tick(90 * 60)
        block = rapport.context_block(CID)
        assert "been away" in block and "camera_who" in block

    def test_a_long_session_is_worth_noticing(self, rapport, clock):
        rapport.note_turn(CID)
        clock.tick(4 * 60 * 60)
        assert "at this together" in rapport.context_block(CID)

    def test_a_short_one_is_not(self, rapport, clock):
        rapport.note_turn(CID)
        clock.tick(300)
        assert "at this together" not in rapport.context_block(CID)

    def test_the_summary_leads_when_there_is_one(self, rapport):
        rapport.note_turn(CID)
        rapport._threads[CID].summary = "Building a robot; exam Thursday."
        assert rapport.context_block(CID).startswith("Earlier in this conversation:")

    def test_it_reads_as_notes_not_orders(self, rapport, clock):
        """Background awareness, not instructions — the brief says what to do."""
        rapport.note_turn(CID, tool_name="weather")
        clock.tick(90 * 60)
        block = rapport.context_block(CID).lower()
        assert "you must" not in block and "always" not in block


class TestRollingSummary:
    @pytest.fixture(autouse=True)
    def a_reachable_model(self, monkeypatch):
        """The suite runs in mock mode; summarizing deliberately refuses there,
        so these tests have to say a real model is answering."""
        monkeypatch.setattr("iris.app.core.config.settings.LLM_MODE", "auto")

    @staticmethod
    def _history(n):
        return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"line {i}"}
                for i in range(n)]

    class FakeGateway:
        """A gateway with a real model behind it."""

        can_answer = True

        def __init__(self, text="They are building a robot. Exam on Thursday.",
                     provider="gemini"):
            self.calls = []
            self.text = text
            self.provider = provider

        async def generate(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return type("R", (), {"content": self.text, "provider_name": self.provider})()

    async def test_a_short_conversation_needs_no_summary(self, rapport):
        gw = self.FakeGateway()
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(10), gw)
        await asyncio.sleep(0.02)
        assert gw.calls == []

    async def test_what_ages_out_of_the_window_is_remembered(self, rapport):
        gw = self.FakeGateway()
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), gw)
        await asyncio.sleep(0.05)
        assert len(gw.calls) == 1
        assert rapport._threads[CID].summary.startswith("They are building a robot")
        assert "Earlier in this conversation" in rapport.context_block(CID)

    async def test_only_the_aged_out_part_is_summarized(self, rapport):
        """The live window is already in the messages; sending it twice is waste."""
        gw = self.FakeGateway()
        rapport.note_turn(CID)
        history = self._history(60)
        rapport.maybe_summarize(CID, history, gw)
        await asyncio.sleep(0.05)
        prompt = gw.calls[0][0]
        assert "line 0" in prompt and "line 59" not in prompt

    async def test_it_does_not_redo_work(self, rapport):
        gw = self.FakeGateway()
        rapport.note_turn(CID)
        for _ in range(3):
            rapport.maybe_summarize(CID, self._history(60), gw)
            await asyncio.sleep(0.03)
        assert len(gw.calls) == 1

    async def test_a_dead_model_is_never_fatal(self, rapport):
        class Broken:
            async def generate(self, *a, **k):
                raise RuntimeError("no key")

        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), Broken())
        await asyncio.sleep(0.05)
        assert rapport._threads[CID].summary == ""
        assert rapport._threads[CID].summarizing is False

    async def test_a_slow_model_is_given_up_on(self, rapport, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.ROLLING_SUMMARY_TIMEOUT_S", 0.01)

        class Slow:
            async def generate(self, *a, **k):
                await asyncio.sleep(5)

        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), Slow())
        await asyncio.sleep(0.1)
        assert rapport._threads[CID].summary == ""

    async def test_it_can_be_switched_off(self, rapport, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.ROLLING_SUMMARY_ENABLED", False)
        gw = self.FakeGateway()
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), gw)
        await asyncio.sleep(0.02)
        assert gw.calls == []

    async def test_the_summary_rolls_instead_of_dropping_the_start(self, rapport):
        """Without feeding the old notes back, the beginning is simply lost."""
        gw = self.FakeGateway()
        rapport.note_turn(CID)
        rapport._threads[CID].summary = "They are called Prakash; building a robot."
        rapport.maybe_summarize(CID, self._history(60), gw)
        await asyncio.sleep(0.05)
        prompt = gw.calls[0][0]
        assert "Notes you already wrote" in prompt and "Prakash" in prompt

    async def test_a_shrinking_history_does_not_stall_it_forever(self, rapport):
        gw = self.FakeGateway()
        rapport.note_turn(CID)
        rapport._threads[CID].summarized_upto = 500      # a high-water mark
        rapport.maybe_summarize(CID, self._history(60), gw)
        await asyncio.sleep(0.05)
        assert len(gw.calls) == 1

    def test_a_failure_to_schedule_does_not_latch_the_gate_shut(self, rapport):
        """No running loop must not mean "never summarize again this session"."""
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), self.FakeGateway())
        assert rapport._threads[CID].summarizing is False

    async def test_the_offline_engine_is_never_mistaken_for_memory(self, rapport):
        """A free tier that rate-limits mid-conversation makes this the common
        case: the gateway falls through to the mock, whose canned reply would
        then be handed to the next real model as something that happened."""
        gw = self.FakeGateway(text="Plan created for intent 'system_info'.",
                              provider="mock")
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), gw)
        await asyncio.sleep(0.05)
        assert rapport._threads[CID].summary == ""
        assert rapport.context_block(CID) == ""

    async def test_it_does_not_even_ask_when_no_model_is_reachable(self, rapport):
        """Rate-limited or offline, the chain falls through to the mock — so
        there is nothing worth asking in the first place."""
        class Unreachable(self.FakeGateway):
            can_answer = False

        gw = Unreachable()
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), gw)
        await asyncio.sleep(0.05)
        assert gw.calls == []

    async def test_a_gateway_that_cannot_say_is_treated_as_unreachable(self, rapport):
        class Odd(self.FakeGateway):
            @property
            def can_answer(self):
                raise RuntimeError("no idea")

        gw = Odd()
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), gw)
        await asyncio.sleep(0.05)
        assert gw.calls == []

    async def test_no_gateway_is_a_silent_no_op(self, rapport):
        rapport.note_turn(CID)
        rapport.maybe_summarize(CID, self._history(60), None)
        await asyncio.sleep(0.02)
        assert rapport._threads[CID].summary == ""


class TestHelpers:
    @pytest.mark.parametrize("minutes,expected", [
        (3, "3 minutes"), (45, "45 minutes"), (120, "2 hours"),
        (90, "1.5 hours"), (60 * 26, "1 day"), (60 * 72, "3 days"),
    ])
    def test_durations_read_like_speech(self, minutes, expected):
        assert _duration(minutes) == expected

    def test_transcript_keeps_only_what_was_said(self):
        out = _transcript([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
            {"role": "tool", "content": "{json}"},
            {"role": "assistant", "content": ""},
        ])
        assert out == "Them: hi\nYou: hey"

    def test_a_long_turn_is_clipped_not_dropped(self):
        out = _transcript([{"role": "user", "content": "x" * 900}])
        assert out.startswith("Them: xxx") and len(out) < 500
