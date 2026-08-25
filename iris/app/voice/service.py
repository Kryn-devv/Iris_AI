"""Voice service: the single entry point for everything spoken.

The service decides *where* speech happens:

* When a server-side TTS engine exists and ``SPEAK_RESPONSES`` is on, sentences
  play on the machine's speakers.
* Always, the sentence is published on the event bus (``voice.speaking``) so
  the web UI can animate the hologram and — in browser-voice mode — speak the
  text itself with ``speechSynthesis``.

Wake-word detection runs client-side in the web UI (continuous browser
recognition matched against ``WAKE_WORDS``), so voice control works with zero
extra installs; the helpers here let any server-side pipeline reuse the same
matching rules.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.voice import stt as stt_module
from iris.app.voice import tts as tts_module

logger = get_logger("voice.service")

_SENTENCE_CLEAN = re.compile(r"[*_`#>\[\]|]")
_URL_RX = re.compile(r"https?://\S+")
_WS = re.compile(r"\s+")


def sanitize_for_speech(text: str, max_chars: int = 500) -> str:
    """Strip markdown noise and URLs so TTS reads naturally."""
    cleaned = _URL_RX.sub("a link", text or "")
    cleaned = _SENTENCE_CLEAN.sub("", cleaned)
    cleaned = _WS.sub(" ", cleaned).strip()
    if len(cleaned) > max_chars:
        cut = cleaned[:max_chars]
        # End on a sentence boundary when possible.
        for stop in (". ", "! ", "? "):
            idx = cut.rfind(stop)
            if idx > max_chars // 2:
                return cut[: idx + 1]
        return cut + "…"
    return cleaned


def strip_wake_word(text: str, wake_words: Optional[list[str]] = None) -> tuple[bool, str]:
    """Detect and remove a leading wake word. Returns (was_woken, remainder)."""
    words = wake_words if wake_words is not None else settings.WAKE_WORDS
    lowered = (text or "").strip()
    for wake in sorted(words, key=len, reverse=True):
        pattern = re.compile(rf"^\s*{re.escape(wake)}\b[,.!?]*\s*", re.IGNORECASE)
        m = pattern.match(lowered)
        if m:
            return True, lowered[m.end():].strip()
    return False, lowered


class VoiceService:
    """Coordinates speech output and transcription."""

    def __init__(self) -> None:
        self._tts_engine: Optional[tts_module.TTSEngineBase] = None
        self._tts_probed = False
        self._speak_lock = asyncio.Lock()
        self.enabled = settings.VOICE_ENABLED

    # -------------------------------------------------------------------- TTS
    def _get_tts(self) -> Optional[tts_module.TTSEngineBase]:
        if not self._tts_probed:
            self._tts_engine = tts_module.pick_engine()
            self._tts_probed = True
            if self._tts_engine:
                logger.info("TTS engine selected: %s", self._tts_engine.name)
            else:
                logger.info("No server TTS engine; browser speechSynthesis mode.")
        return self._tts_engine

    def refresh_engines(self) -> None:
        """Re-probe engines after settings or installs change."""
        self._tts_probed = False

    @property
    def tts_engine_name(self) -> str:
        engine = self._get_tts()
        return engine.name if engine else "browser"

    @property
    def stt_engine_name(self) -> str:
        engine = stt_module.pick_engine()
        return engine.name if engine else "browser"

    async def speak(self, text: str, *, interrupt: bool = False) -> dict[str, Any]:
        """Speak a sentence (server-side when possible) and notify all UIs."""
        sentence = sanitize_for_speech(text)
        if not sentence:
            return {"spoken": False, "engine": None, "text": ""}

        engine = self._get_tts() if (self.enabled and settings.SPEAK_RESPONSES) else None
        default_event_bus.publish(
            Topics.VOICE_SPEAKING,
            {"text": sentence, "engine": engine.name if engine else "browser"},
        )

        spoken = False
        if engine is not None:
            async with self._speak_lock:
                try:
                    spoken = await engine.speak(sentence)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("TTS engine %s failed: %s", engine.name, exc)
        return {"spoken": spoken, "engine": engine.name if engine else "browser", "text": sentence}

    async def synthesize(self, text: str) -> Optional[str]:
        """Produce an audio file for a sentence (for phone clients)."""
        sentence = sanitize_for_speech(text)
        engine = self._get_tts()
        if engine is None or not sentence:
            return None
        path = await engine.synthesize(sentence)
        return str(path) if path else None

    # -------------------------------------------------------------------- STT
    async def transcribe(self, audio_bytes: bytes, content_type: str = "") -> Optional[dict[str, Any]]:
        """Transcribe an uploaded audio blob."""
        result = await stt_module.transcribe_upload(audio_bytes, content_type)
        if result is None:
            return None
        default_event_bus.publish(Topics.VOICE_FINAL, result.to_dict())
        return result.to_dict()

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "speak_responses": settings.SPEAK_RESPONSES,
            "tts_engine": self.tts_engine_name,
            "stt_engine": self.stt_engine_name,
            "wake_words": settings.WAKE_WORDS,
            "wake_word_enabled": settings.WAKE_WORD_ENABLED,
            "browser_voice": self.tts_engine_name == "browser" or self.stt_engine_name == "browser",
        }


default_voice_service = VoiceService()
