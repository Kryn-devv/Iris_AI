"""Tests for the robot's OLED face — the tool, the inference, the auto-push.

The interesting part is not the HTTP call, it is the two pure functions that
decide what the face does on its own. ``infer_emotion`` runs on the speech
path for every sentence IRIS says, and ``estimate_speech_ms`` is what stops a
lost packet leaving the eyes bouncing forever.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from iris.app.core.bus import EventBus, Topics
from iris.app.nlu.engine import IntentEngine
from iris.app.services.face_presence import FacePresenceService
from iris.app.tools.devices import esp32 as esp32_mod
from iris.app.tools.devices.face import (
    EMOTIONS,
    MAX_SPEAK_MS,
    MIN_SPEAK_MS,
    SYNONYMS,
    FaceEmotionTool,
    estimate_speech_ms,
    infer_emotion,
    normalize_emotion,
)
from iris.app.tools.devices.registry import Device, DeviceRegistry


@pytest.fixture()
def registry(tmp_path):
    return DeviceRegistry(path=tmp_path / "devices.json")


@pytest.fixture()
def face_registry(registry):
    registry.add(Device(name="face", base_url="http://192.168.1.70", kind="face"))
    return registry


@pytest.fixture()
def fake_face(monkeypatch):
    """A fake face node that records every request it receives."""
    calls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        if request.url.path == "/face":
            return httpx.Response(200, json={"ok": True, "face": {"emotion": "happy"}})
        if request.url.path == "/status":
            return httpx.Response(200, json={"name": "face", "kind": "face"})
        return httpx.Response(404, json={"error": "unknown endpoint"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(esp32_mod.httpx, "AsyncClient", fake_client)
    return calls


# --------------------------------------------------------------- emotion names
class TestEmotionNames:
    def test_canonical_names_pass_through(self):
        for name in EMOTIONS:
            assert normalize_emotion(name) == name

    def test_case_and_spacing_are_forgiven(self):
        assert normalize_emotion("  HAPPY ") == "happy"
        assert normalize_emotion("Sleepy") == "sleepy"

    def test_synonyms_resolve(self):
        assert normalize_emotion("smile") == "happy"
        assert normalize_emotion("mad") == "angry"
        assert normalize_emotion("khush") == "happy"
        assert normalize_emotion("udaas") == "sad"
        assert normalize_emotion("gussa") == "angry"

    def test_every_synonym_resolves_to_a_real_emotion(self):
        for word, target in SYNONYMS.items():
            assert target in EMOTIONS, f"'{word}' -> unknown '{target}'"
            assert normalize_emotion(word) == target

    def test_unknown_names_are_rejected_not_guessed(self):
        for bad in ("", "   ", "grumpy", "banana", "happpy", None):
            assert normalize_emotion(bad) is None


# ------------------------------------------------------------ speech duration
class TestSpeechDuration:
    def test_empty_text_is_zero(self):
        assert estimate_speech_ms("") == 0
        assert estimate_speech_ms("   ") == 0

    def test_longer_sentences_take_longer(self):
        short = estimate_speech_ms("Done.")
        medium = estimate_speech_ms("I have opened YouTube for you.")
        long = estimate_speech_ms(" ".join(["word"] * 60))
        assert short < medium < long

    def test_bounded_at_both_ends(self):
        assert estimate_speech_ms("Hi") >= MIN_SPEAK_MS
        assert estimate_speech_ms(" ".join(["word"] * 5000)) <= MAX_SPEAK_MS

    def test_never_exceeds_the_firmware_ceiling(self):
        """Past it the board clamps anyway; agreeing keeps /status honest."""
        for words in (1, 10, 100, 1000, 10_000):
            assert 0 < estimate_speech_ms(" ".join(["word"] * words)) <= MAX_SPEAK_MS

    def test_punctuation_adds_pause_time(self):
        assert estimate_speech_ms("yes no maybe") < estimate_speech_ms("yes, no, maybe.")

    def test_a_realistic_sentence_is_in_a_plausible_range(self):
        ms = estimate_speech_ms("Sure, I have opened YouTube in your browser.")
        assert 2000 <= ms <= 6000, ms


# --------------------------------------------------------------- inference
class TestEmotionInference:
    @pytest.mark.parametrize("text,expected", [
        ("Sorry, I could not find that file.", "sad"),
        ("Unfortunately that failed.", "sad"),
        ("I'm afraid I can't do that.", "sad"),
        ("Done! Your presentation is ready.", "excited"),
        ("All set — YouTube is open.", "excited"),
        ("Ho gaya, file mil gaya.", "excited"),
        ("Hello! Good morning.", "happy"),
        ("Namaste, main IRIS hoon.", "happy"),
        ("Thanks for that.", "happy"),
        ("Let me check the weather for you.", "thinking"),
        ("One moment, searching now.", "thinking"),
        ("Ruko, dekh raha hoon.", "thinking"),
        ("Wow, that is a big file.", "surprised"),
        ("Good night.", "sleepy"),
        ("I'm not sure what you meant.", "confused"),
        ("That is not allowed.", "angry"),
        ("I love that idea", "love"),
        ("The time is four o'clock.", "neutral"),
    ])
    def test_reads_the_sentence(self, text, expected):
        assert infer_emotion(text) == expected

    def test_a_cue_beats_punctuation(self):
        """"Sorry, that failed!" is apologetic despite the exclamation mark —
        reading the mark first would put a delighted face on bad news."""
        assert infer_emotion("Sorry, that failed!") == "sad"
        assert infer_emotion("Unfortunately I could not open it!") == "sad"

    def test_punctuation_is_only_a_tie_breaker(self):
        assert infer_emotion("Is that the one you meant?") == "thinking"
        assert infer_emotion("Four hundred and twenty!") == "excited"

    def test_empty_text_returns_the_default(self):
        assert infer_emotion("") == "neutral"
        assert infer_emotion("   ", default="listening") == "listening"

    def test_always_returns_a_real_emotion(self):
        samples = [
            "", "hello", "sorry", "done", "wow", "?", "!", "...",
            "The quick brown fox.", "गैस का स्तर सामान्य है", "42",
            "a" * 500, "\n\t ", "Sorry! Done! Wow!",
        ]
        for text in samples:
            assert infer_emotion(text) in EMOTIONS

    def test_whole_words_only(self):
        """'sad' must not fire inside 'saddle', nor 'hi' inside 'this'."""
        assert infer_emotion("The saddle is in the shed.") == "neutral"
        assert infer_emotion("This is the thing.") == "neutral"

    def test_never_raises_on_odd_input(self):
        for text in (None, 123, "", "\x00", "🙂🙂🙂"):
            assert infer_emotion(text) in EMOTIONS


# ------------------------------------------------------------------ the tool
class TestFaceEmotionTool:
    @pytest.mark.asyncio
    async def test_sets_an_expression(self, face_registry, fake_face):
        res = await FaceEmotionTool(face_registry).execute(emotion="happy")
        assert res.success
        assert res.result["emotion"] == "happy"
        assert "happy" in res.speech
        assert fake_face and fake_face[0].params["emotion"] == "happy"

    @pytest.mark.asyncio
    async def test_resolves_a_synonym_before_calling(self, face_registry, fake_face):
        """The board only knows canonical names, so IRIS must translate first."""
        res = await FaceEmotionTool(face_registry).execute(emotion="khush")
        assert res.success
        assert res.result["emotion"] == "happy"
        assert fake_face[0].params["emotion"] == "happy"

    @pytest.mark.asyncio
    async def test_unknown_emotion_fails_without_a_network_call(self, face_registry, fake_face):
        res = await FaceEmotionTool(face_registry).execute(emotion="grumpy")
        assert not res.success
        assert fake_face == [], "a bad name must not reach the board"

    @pytest.mark.asyncio
    async def test_seconds_becomes_a_hold(self, face_registry, fake_face):
        await FaceEmotionTool(face_registry).execute(emotion="wink", seconds=2)
        assert fake_face[0].params["hold_ms"] == "2000"

    @pytest.mark.asyncio
    async def test_look_directions_map_to_coordinates(self, face_registry, fake_face):
        await FaceEmotionTool(face_registry).execute(emotion="neutral", look="left")
        params = fake_face[0].params
        assert int(params["look_x"]) < 0
        assert params["look_y"] == "0"

    @pytest.mark.asyncio
    async def test_blink_is_forwarded(self, face_registry, fake_face):
        await FaceEmotionTool(face_registry).execute(emotion="neutral", blink=True)
        assert fake_face[0].params["blink"] == "1"

    @pytest.mark.asyncio
    async def test_no_face_registered_explains_what_to_do(self, registry, fake_face):
        res = await FaceEmotionTool(registry).execute(emotion="happy")
        assert not res.success
        assert "add device" in (res.error or "")

    @pytest.mark.asyncio
    async def test_every_emotion_is_accepted_by_the_tool(self, face_registry, fake_face):
        tool = FaceEmotionTool(face_registry)
        for emotion in EMOTIONS:
            res = await tool.execute(emotion=emotion)
            assert res.success, f"{emotion}: {res.error}"
            assert res.result["emotion"] == emotion

    def test_the_schema_advertises_exactly_the_real_emotions(self):
        enum = FaceEmotionTool().input_schema.properties["emotion"]["enum"]
        assert tuple(enum) == EMOTIONS


# ------------------------------------------------------- automatic expression
class TestFacePresenceService:
    """The face must follow the voice without anyone asking it to — and must
    never be able to delay or break the voice while doing so."""

    @pytest.mark.asyncio
    async def test_speaking_pushes_an_inferred_emotion(self, face_registry, fake_face):
        bus = EventBus()
        service = FacePresenceService(bus=bus, registry=face_registry)
        await service.start()
        try:
            bus.publish(Topics.VOICE_SPEAKING, {"text": "Done! Your file is ready."})
            await asyncio.sleep(0.05)
        finally:
            await service.stop()

        assert fake_face, "nothing was pushed to the face"
        params = fake_face[0].params
        assert params["emotion"] == "excited"
        assert int(params["speak_ms"]) > 0

    @pytest.mark.asyncio
    async def test_the_wake_word_makes_it_listen(self, face_registry, fake_face):
        bus = EventBus()
        service = FacePresenceService(bus=bus, registry=face_registry)
        await service.start()
        try:
            bus.publish(Topics.VOICE_WAKE, {})
            await asyncio.sleep(0.05)
        finally:
            await service.stop()
        assert fake_face[0].params["emotion"] == "listening"

    @pytest.mark.asyncio
    async def test_a_repeated_sentence_is_not_pushed_twice(self, face_registry, fake_face):
        """The same sentence is published again when server audio falls back to
        the browser; the face should not restart its animation for the echo."""
        bus = EventBus()
        service = FacePresenceService(bus=bus, registry=face_registry)
        await service.start()
        try:
            for _ in range(3):
                bus.publish(Topics.VOICE_SPEAKING, {"text": "Opening YouTube."})
                await asyncio.sleep(0.02)
        finally:
            await service.stop()
        assert len(fake_face) == 1, f"pushed {len(fake_face)} times"

    @pytest.mark.asyncio
    async def test_no_face_device_is_a_silent_no_op(self, registry, fake_face):
        bus = EventBus()
        service = FacePresenceService(bus=bus, registry=registry)
        await service.start()
        try:
            bus.publish(Topics.VOICE_SPEAKING, {"text": "Hello there."})
            await asyncio.sleep(0.05)
        finally:
            await service.stop()
        assert fake_face == []

    @pytest.mark.asyncio
    async def test_an_empty_sentence_is_ignored(self, face_registry, fake_face):
        bus = EventBus()
        service = FacePresenceService(bus=bus, registry=face_registry)
        await service.start()
        try:
            bus.publish(Topics.VOICE_SPEAKING, {"text": "   "})
            bus.publish(Topics.VOICE_SPEAKING, {})
            await asyncio.sleep(0.05)
        finally:
            await service.stop()
        assert fake_face == []

    @pytest.mark.asyncio
    async def test_an_unreachable_face_never_raises(self, face_registry, monkeypatch):
        """A face node that has been unplugged must not break the voice path."""
        async def boom(*args, **kwargs):
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr("iris.app.services.face_presence.push_face", boom)
        bus = EventBus()
        service = FacePresenceService(bus=bus, registry=face_registry)
        await service.start()
        try:
            for _ in range(5):
                bus.publish(Topics.VOICE_SPEAKING, {"text": f"Attempt {_}."})
                await asyncio.sleep(0.02)
        finally:
            await service.stop()      # completes without error = the assertion

    @pytest.mark.asyncio
    async def test_repeated_failures_back_off(self, face_registry, monkeypatch):
        """Otherwise a missing node logs once per spoken sentence, forever."""
        attempts = []

        async def boom(*args, **kwargs):
            attempts.append(1)
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr("iris.app.services.face_presence.push_face", boom)
        bus = EventBus()
        service = FacePresenceService(bus=bus, registry=face_registry)
        await service.start()
        try:
            for i in range(12):
                bus.publish(Topics.VOICE_SPEAKING, {"text": f"Sentence number {i}."})
                await asyncio.sleep(0.02)
        finally:
            await service.stop()
        assert len(attempts) <= 4, f"kept retrying {len(attempts)} times"

    @pytest.mark.asyncio
    async def test_stop_is_safe_before_start_and_twice(self, face_registry):
        service = FacePresenceService(registry=face_registry)
        await service.stop()
        await service.start()
        await service.stop()
        await service.stop()


# ---------------------------------------------------------------------- NLU
class TestFaceIntents:
    @pytest.fixture()
    def engine(self):
        return IntentEngine()

    @pytest.mark.parametrize("text,emotion", [
        ("look happy", "happy"),
        ("be happy", "happy"),
        ("show happy eyes", "happy"),
        ("happy", "happy"),
        ("look sad", "sad"),
        ("be angry", "angry"),
        ("show me love", "love"),
        ("be sleepy", "sleepy"),
        ("look confused", "confused"),
        ("dizzy", "dizzy"),
        ("khush ho jao", "happy"),
        ("gussa dikhao", "angry"),
        ("udaas", "sad"),
    ])
    def test_expressions_route_to_the_face(self, engine, text, emotion):
        match = engine.match(text)
        assert match is not None, f"{text!r} matched nothing"
        assert match.tool_name == "face_emotion"
        assert match.arguments["emotion"] == emotion

    @pytest.mark.parametrize("text", ["wink", "wink at me", "aankh maaro",
                                      "give me a wink"])
    def test_wink(self, engine, text):
        match = engine.match(text)
        assert match.tool_name == "face_emotion"
        assert match.arguments["emotion"] == "wink"

    @pytest.mark.parametrize("text", ["blink", "blink your eyes", "palak jhapkao"])
    def test_blink(self, engine, text):
        match = engine.match(text)
        assert match.tool_name == "face_emotion"
        assert match.arguments["blink"] is True

    @pytest.mark.parametrize("text,direction", [
        ("look left", "left"), ("look to the right", "right"),
        ("eyes up", "up"), ("look down", "down"),
        ("look away", "away"), ("look at me", "centre"),
        ("look straight", "centre"),
    ])
    def test_gaze(self, engine, text, direction):
        match = engine.match(text)
        assert match.tool_name == "face_emotion"
        assert match.arguments["look"] == direction

    @pytest.mark.parametrize("text,tool", [
        ("robot forward", "device_motor"),
        ("stop the robot", "device_motor"),
        ("is there any motion", "device_sensors"),
        ("gas level", "device_sensors"),
        ("turn on the fan", "device_switch"),
        ("open youtube", "open_website"),
        ("mute", "volume"),
        ("volume up", "volume"),
        ("next song", "media_control"),
        ("pause", "media_control"),
        ("screenshot", "take_screenshot"),
    ])
    def test_the_broad_mood_rule_does_not_hijack_other_commands(self, engine, text, tool):
        """"be X" / a bare adjective is a wide pattern; its builder rejects any
        word that is not an emotion, and these must still reach their own tool."""
        match = engine.match(text)
        assert match is not None, f"{text!r} stopped matching"
        assert match.tool_name == tool

    @pytest.mark.parametrize("text", ["left", "right", "up", "down", "hello",
                                      "youtube", "copy", "paste", "lock"])
    def test_bare_non_emotion_words_are_not_claimed_by_the_face(self, engine, text):
        match = engine.match(text)
        if match is not None:
            assert match.tool_name != "face_emotion", f"{text!r} was hijacked"
