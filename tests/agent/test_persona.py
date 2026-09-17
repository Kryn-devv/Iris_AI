"""Her voice: varied, situational, and incapable of losing what a tool said.

The one invariant that matters more than any wording: a tool reported
something real, and whatever she does to the packaging, the content survives.
A paraphrased sensor reading is an invented sensor reading.
"""

from __future__ import annotations

import random

import pytest

from iris.app.agent.persona import (
    Voice,
    character_prompt,
    normalize_style,
    now_label,
    user_label,
    user_possessive,
)
from iris.app.language.models import LanguageStyle

E = LanguageStyle.ENGLISH
H = LanguageStyle.HINDI
HG = LanguageStyle.HINGLISH


@pytest.fixture()
def voice():
    return Voice(rng=random.Random(7))


#: Real confirmations from across the tool suite.
INFORMATIVE = [
    "Opened YouTube.",
    "It's 24.5 degrees and 60 percent humidity.",
    "Yes, that's you.",
    "Front left is 32 centimetres away.",
    "Timer set for 5 minutes.",
    "I know one face: Prakash (you).",
    "Done: turned around, then drove until the board was 33 centimetres ahead.",
    "I ran 'take a U-turn' 100 times. 100 out of 100 came out identical.",
]


class TestNothingIsLost:
    @pytest.mark.parametrize("text", INFORMATIVE)
    @pytest.mark.parametrize("style", [E, H, HG])
    def test_content_survives_every_dressing(self, voice, text, style):
        """Whatever she adds, the tool's own words are still in there."""
        for _ in range(12):
            out = voice.acknowledge(text, style=style, returning=True, repeated=True)
            core = text.rstrip(".")
            assert core.lower() in out.lower(), (out, text)

    @pytest.mark.parametrize("text", INFORMATIVE)
    def test_numbers_are_never_touched(self, voice, text):
        digits = [c for c in text if c.isdigit()]
        for _ in range(8):
            out = voice.acknowledge(text, style=E)
            assert [c for c in out if c.isdigit()] == digits

    def test_a_failure_keeps_its_reason(self, voice):
        out = voice.acknowledge("The camera at 192.168.1.37 did not answer in time.",
                                style=E, success=False)
        assert "192.168.1.37" in out and "did not answer in time" in out

    def test_proper_nouns_keep_their_capitals(self, voice):
        seen = {voice.acknowledge("Opened YouTube.", style=E) for _ in range(20)}
        assert all("YouTube" in s for s in seen), seen
        assert any(s != "Opened YouTube." for s in seen), "never varied at all"


class TestVariety:
    def test_she_never_says_the_same_thing_twice_running(self, voice):
        lines = [voice.done(E) for _ in range(40)]
        assert all(a != b for a, b in zip(lines, lines[1:])), lines

    def test_a_bare_ack_is_replaced_not_echoed(self, voice):
        outs = {voice.acknowledge("Done.", style=E) for _ in range(30)}
        assert len(outs) >= 5, outs

    def test_variety_holds_in_every_register(self, voice):
        for style in (E, H, HG):
            outs = {voice.acknowledge("Done.", style=style) for _ in range(30)}
            assert len(outs) >= 3, (style, outs)

    def test_failures_vary_too(self, voice):
        outs = {voice.acknowledge("", style=E, success=False) for _ in range(30)}
        assert len(outs) >= 4, outs

    def test_a_short_bucket_still_never_immediately_repeats(self):
        """Even with only three options, the last line is never reused."""
        v = Voice(rng=random.Random(1))
        lines = [v.pick(("a", "b", "c"), bucket="t") for _ in range(30)]
        assert all(a != b for a, b in zip(lines, lines[1:])), lines


class TestSituation:
    def test_coming_back_is_greeted(self, voice):
        outs = [voice.acknowledge("Opened YouTube.", style=E, returning=True) for _ in range(8)]
        assert any(o.lower().startswith(("hey", "back", "there you are", "welcome")) for o in outs), outs

    def test_asking_twice_is_noticed(self, voice):
        outs = [voice.acknowledge("Opened YouTube.", style=E, repeated=True) for _ in range(8)]
        assert any(o.lower().startswith(("again", "once more", "round two")) for o in outs), outs

    def test_mid_burst_she_gets_out_of_the_way(self, voice):
        """Firing commands fast, a person answers shorter — so does she."""
        for _ in range(10):
            assert voice.acknowledge("Opened YouTube.", style=E, terse=True) == "Opened YouTube."
        terse = {voice.acknowledge("Done.", style=E, terse=True) for _ in range(20)}
        normal = {voice.acknowledge("Done.", style=E) for _ in range(20)}
        assert max(len(t) for t in terse) <= max(len(n) for n in normal)

    def test_a_failure_is_never_dressed_up_as_a_greeting(self, voice):
        for _ in range(20):
            out = voice.acknowledge("Could not reach the camera.", style=E,
                                    success=False, returning=True, repeated=True)
            assert not out.lower().startswith(("hey", "welcome", "again"))
            assert "could not reach the camera" in out.lower()


class TestOneOpenerOnly:
    """"Hey — okay — opened YouTube" is nobody's speech."""

    @pytest.mark.parametrize("returning,repeated", [(True, False), (False, True), (True, True)])
    def test_a_situational_opener_never_stacks_with_a_lead_in(self, voice, returning, repeated):
        for _ in range(25):
            out = voice.acknowledge("Opened YouTube.", style=E,
                                    returning=returning, repeated=repeated)
            assert out.count(" — ") <= 1, out

    def test_a_bare_ack_gets_no_run_up(self, voice):
        for _ in range(25):
            assert " — " not in voice.acknowledge("Done.", style=E)

    def test_at_most_one_opener_in_any_register(self, voice):
        for style in (E, H, HG):
            for _ in range(25):
                out = voice.acknowledge("Opened YouTube.", style=style, returning=True)
                assert out.count(" — ") <= 1, out


class TestItReadsAloud:
    """Bugs you only hear when you say the sentence out loud."""

    def test_a_sentence_opener_keeps_the_next_capital(self, voice):
        """"Welcome back. opened YouTube." is not a sentence."""
        for _ in range(40):
            out = voice.acknowledge("Opened YouTube.", style=E, returning=True)
            for opener in ("Back. ", "There you are. ", "Welcome back. "):
                if out.startswith(opener):
                    assert out[len(opener)].isupper(), out

    def test_a_dashed_opener_runs_into_lower_case(self, voice):
        for _ in range(40):
            out = voice.acknowledge("Opened YouTube.", style=E)
            if " — " in out:
                assert out.split(" — ", 1)[1].startswith("opened"), out

    def test_never_two_dashes_in_one_breath(self, voice):
        """The tool already used a dash; a dashed opener would make it soup."""
        text = "Could not reach the camera at 192.168.1.37 — is it powered on?"
        for _ in range(40):
            out = voice.acknowledge(text, style=E, success=False)
            assert out.count(" — ") == 1, out
            assert text in out

    def test_a_dashed_sentence_still_gets_a_varied_opener(self, voice):
        """Dropping the opener would be safe and dull; it becomes a sentence."""
        text = "Could not reach the camera at 192.168.1.37 — is it powered on?"
        outs = {voice.acknowledge(text, style=E, success=False) for _ in range(40)}
        assert len(outs) >= 3, outs
        for out in outs:
            assert out.count(" — ") == 1 and text in out

    def test_a_dash_free_failure_can_still_get_an_opener(self, voice):
        outs = {voice.acknowledge("The file is locked.", style=E, success=False)
                for _ in range(40)}
        assert any(" — " in o for o in outs), outs


class TestRegisters:
    def test_hinglish_answers_in_hinglish(self, voice):
        outs = {voice.acknowledge("Done.", style=HG) for _ in range(30)}
        assert any(any(w in o.lower() for w in ("ho gaya", "kar diya", "set hai", "theek")) for o in outs), outs

    def test_hindi_answers_in_devanagari(self, voice):
        outs = {voice.acknowledge("Done.", style=H) for _ in range(30)}
        assert any(any("ऀ" <= ch <= "ॿ" for ch in o) for o in outs), outs

    @pytest.mark.parametrize("given,expected", [
        (LanguageStyle.MIXED, HG), (LanguageStyle.UNKNOWN, E), ("HINGLISH", HG),
        ("hindi", H), (None, E), ("nonsense", E), (E, E),
    ])
    def test_style_coercion(self, given, expected):
        assert normalize_style(given) is expected

    def test_an_unknown_register_still_answers(self, voice):
        assert voice.acknowledge("Done.", style="klingon").strip()


class TestCharacterBrief:
    def test_it_fills_its_slots(self, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.USER_NAME", "Prakash")
        monkeypatch.setattr("iris.app.core.config.settings.ASSISTANT_NAME", "Iris")
        prompt = character_prompt()
        assert "{name}" not in prompt and "{user}" not in prompt and "{now}" not in prompt
        assert "Prakash" in prompt and prompt.startswith("You are Iris")

    def test_without_a_name_it_still_reads(self, monkeypatch):
        """No name set must still produce English, not "work for's assistant"."""
        monkeypatch.setattr("iris.app.core.config.settings.USER_NAME", "")
        prompt = character_prompt()
        assert "the person you work for" in prompt
        assert "work for's" not in prompt and "for' assistant" not in prompt
        assert "a person's assistant" in prompt
        assert user_label() == "the person you work for"

    @pytest.mark.parametrize("name,expected", [
        ("Prakash", "Prakash's"), ("Chris", "Chris'"), ("", "a person's"), ("  ", "a person's"),
    ])
    def test_the_possessive_is_grammatical(self, monkeypatch, name, expected):
        monkeypatch.setattr("iris.app.core.config.settings.USER_NAME", name)
        assert user_possessive() == expected

    def test_with_a_name_the_opening_is_warm(self, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.USER_NAME", "Prakash")
        assert "Prakash's assistant" in character_prompt()

    def test_it_forbids_the_vending_machine_phrases(self):
        prompt = character_prompt()
        for banned in ("As an AI", "Certainly!", "Great question!", "I'd be happy to help"):
            assert banned in prompt, f"{banned} should be named as forbidden"
        assert "let me know if you need anything else" in prompt.lower()

    def test_it_tells_her_she_is_heard_not_read(self):
        prompt = character_prompt().lower()
        assert "no bullet points" in prompt and "no markdown" in prompt

    def test_the_clock_reads_like_a_person_said_it(self):
        import datetime
        label = now_label(datetime.datetime(2026, 9, 17, 9, 5))
        assert "September" in label and "9:05" in label and "am" in label
        assert "AM" not in label

    def test_she_only_claims_a_body_she_has(self, monkeypatch):
        """Nothing is hollower than an assistant describing eyes it hasn't got."""
        import iris.app.agent.persona as persona_mod

        class FakeRegistry:
            def __init__(self, kinds): self._kinds = kinds
            def list(self): return [type("D", (), {"kind": k})() for k in self._kinds]

        monkeypatch.setattr("iris.app.tools.devices.registry.default_device_registry",
                            FakeRegistry([]))
        assert "body" not in persona_mod._body_clause()

        monkeypatch.setattr("iris.app.tools.devices.registry.default_device_registry",
                            FakeRegistry(["motor", "camera"]))
        clause = persona_mod._body_clause()
        assert "wheeled robot base" in clause and "camera" in clause
        assert "OLED eyes" not in clause          # no face board registered

    def test_a_broken_registry_never_breaks_the_prompt(self, monkeypatch):
        import iris.app.agent.persona as persona_mod

        class Exploding:
            def list(self): raise RuntimeError("no devices file")

        monkeypatch.setattr("iris.app.tools.devices.registry.default_device_registry", Exploding())
        assert persona_mod._body_clause() == ""
        assert character_prompt().startswith("You are")


class TestPromptAssembly:
    def test_context_is_appended_under_a_heading(self):
        from iris.app.agent.prompts import get_system_prompt

        out = get_system_prompt("They are called Prakash.")
        assert "What you know right now" in out and "They are called Prakash." in out

    def test_the_suffix_lands_last_for_local_models(self, monkeypatch):
        from iris.app.agent.prompts import get_system_prompt

        monkeypatch.setattr("iris.app.core.config.settings.PROMPT_SUFFIX", "/no_think")
        assert get_system_prompt().rstrip().endswith("/no_think")

    def test_no_suffix_by_default(self, monkeypatch):
        from iris.app.agent.prompts import get_system_prompt

        monkeypatch.setattr("iris.app.core.config.settings.PROMPT_SUFFIX", "")
        assert not get_system_prompt().rstrip().endswith("/no_think")
