"""Female/language-aware TTS: voice picking, edge voice table, language plumbing."""

from types import SimpleNamespace

from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.voice import tts
from iris.app.voice.service import VoiceService


def _voice(id_, name):
    return SimpleNamespace(id=id_, name=name)


# ------------------------------------------------------------ pyttsx3 picking
def test_pick_pyttsx3_voice_prefers_female_english(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "")
    voices = [_voice("v-david", "Microsoft David"), _voice("v-zira", "Microsoft Zira")]
    assert tts.pick_pyttsx3_voice(voices, "en") == "v-zira"


def test_pick_pyttsx3_voice_prefers_heera_for_hindi(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "")
    voices = [
        _voice("v-david", "Microsoft David"),
        _voice("v-zira", "Microsoft Zira"),
        _voice("v-heera", "Microsoft Heera"),
    ]
    assert tts.pick_pyttsx3_voice(voices, "hi") == "v-heera"
    # Hinglish counts as Hindi for voice selection.
    assert tts.pick_pyttsx3_voice(voices, "hinglish") == "v-heera"


def test_pick_pyttsx3_voice_falls_back_to_female_when_no_hindi(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "")
    voices = [_voice("v-david", "Microsoft David"), _voice("v-zira", "Microsoft Zira")]
    assert tts.pick_pyttsx3_voice(voices, "hi") == "v-zira"


def test_pick_pyttsx3_voice_honours_configured_name(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "david")
    voices = [_voice("v-david", "Microsoft David"), _voice("v-zira", "Microsoft Zira")]
    assert tts.pick_pyttsx3_voice(voices, "en") == "v-david"


def test_pick_pyttsx3_voice_none_when_no_match(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "")
    voices = [_voice("v-david", "Microsoft David")]
    assert tts.pick_pyttsx3_voice(voices, "en") is None


# ------------------------------------------------------------- edge voice map
def test_edge_voice_table_maps_hinglish_to_swara(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "")
    engine = tts.EdgeTTSEngine()
    assert engine.VOICE_BY_LANGUAGE["hinglish"] == "hi-IN-SwaraNeural"
    assert engine._voice_for("hinglish") == "hi-IN-SwaraNeural"
    assert engine._voice_for("hi") == "hi-IN-SwaraNeural"
    assert engine._voice_for("hi-IN") == "hi-IN-SwaraNeural"
    assert engine._voice_for("en") == "en-US-AriaNeural"
    assert engine._voice_for("") == "en-US-AriaNeural"


def test_edge_voice_configured_name_wins(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "en-GB-SoniaNeural")
    engine = tts.EdgeTTSEngine()
    assert engine._voice_for("hi") == "en-GB-SoniaNeural"


# --------------------------------------------------------- language plumbing
async def test_base_speak_forwards_language_to_synthesize():
    seen = {}

    class StubEngine(tts.TTSEngineBase):
        name = "stub"

        def available(self) -> bool:
            return True

        async def synthesize(self, text, language="en"):
            seen["text"] = text
            seen["language"] = language
            return None

    spoken = await StubEngine().speak("namaste", "hi")
    assert spoken is False  # no audio file produced -> nothing played
    assert seen == {"text": "namaste", "language": "hi"}


async def test_service_republishes_browser_when_engine_fails(monkeypatch):
    class FailingEngine(tts.TTSEngineBase):
        name = "failing"

        def available(self) -> bool:
            return True

        async def speak(self, text, language="en") -> bool:
            return False

    service = VoiceService()
    service.enabled = True
    service._tts_engine = FailingEngine()
    service._tts_probed = True
    monkeypatch.setattr(settings, "SPEAK_RESPONSES", True)

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        default_event_bus, "publish", lambda topic, payload=None: events.append((topic, payload or {}))
    )

    result = await service.speak("hello there", language="hi")
    assert result["spoken"] is False
    assert result["engine"] == "browser"

    speaking = [p for t, p in events if t == Topics.VOICE_SPEAKING]
    assert speaking[0]["engine"] == "failing"
    assert speaking[-1]["engine"] == "browser"
    assert speaking[-1]["text"] == "hello there"
    assert speaking[-1]["language"] == "hi"


async def test_service_does_not_republish_when_engine_speaks(monkeypatch):
    class OkEngine(tts.TTSEngineBase):
        name = "ok"

        def available(self) -> bool:
            return True

        async def speak(self, text, language="en") -> bool:
            return True

    service = VoiceService()
    service.enabled = True
    service._tts_engine = OkEngine()
    service._tts_probed = True
    monkeypatch.setattr(settings, "SPEAK_RESPONSES", True)

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        default_event_bus, "publish", lambda topic, payload=None: events.append((topic, payload or {}))
    )

    result = await service.speak("hi there")
    assert result["spoken"] is True and result["engine"] == "ok"
    speaking = [p for t, p in events if t == Topics.VOICE_SPEAKING]
    assert len(speaking) == 1 and speaking[0]["engine"] == "ok"


# ---------------------------------------------------------------------- status
def test_status_reports_voice_and_languages(monkeypatch):
    monkeypatch.setattr(settings, "TTS_VOICE", "")
    service = VoiceService()
    service._tts_engine = None
    service._tts_probed = True
    status = service.status()
    assert status["tts_voice"] == "auto (female)"
    assert status["languages"] == ["en", "hi", "hinglish"]

    monkeypatch.setattr(settings, "TTS_VOICE", "Heera")
    assert service.status()["tts_voice"] == "Heera"
