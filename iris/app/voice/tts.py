"""Text-to-speech engines with layered fallbacks.

Engine order (configurable via ``TTS_ENGINE``):

1. **piper** — high-quality offline neural TTS (the ``piper`` binary).
2. **pyttsx3** — offline OS voices (SAPI5 on Windows, NSSpeech on macOS,
   espeak on Linux).
3. **espeak** — direct espeak/espeak-ng binary.
4. **edge** — Microsoft Edge neural voices via the free ``edge-tts`` package.
5. **gtts** — Google Translate voices via ``gTTS``.
6. **browser** — no server audio at all: IRIS publishes the sentence on the
   event bus and the web UI speaks it with the browser's own
   ``speechSynthesis``. Always available, needs nothing installed.

Every synth method returns a file path (or ``None`` for engines that play
directly), so callers can also hand audio to the phone/web clients.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import has_binary, is_macos, try_import

logger = get_logger("voice.tts")


class TTSEngineBase:
    """Interface for one TTS backend."""

    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    async def synthesize(self, text: str) -> Optional[Path]:
        """Produce an audio file for ``text`` (None when engine plays directly)."""
        raise NotImplementedError

    async def speak(self, text: str) -> bool:
        """Speak ``text`` on the server's speakers. Returns success."""
        path = await self.synthesize(text)
        if path is None:
            return False
        return await play_audio_file(path)


def _out_path(suffix: str) -> Path:
    directory = paths.recordings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"tts_{int(time.time())}_{uuid.uuid4().hex[:6]}{suffix}"


async def play_audio_file(path: Path) -> bool:
    """Play an audio file using whatever the host provides."""
    def _play_blocking() -> bool:
        sd = try_import("sounddevice")
        sf = try_import("soundfile")
        if sd is not None and sf is not None and path.suffix.lower() == ".wav":
            try:
                data, rate = sf.read(str(path), dtype="float32")
                sd.play(data, rate)
                sd.wait()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("sounddevice playback failed: %s", exc)
        for player in (
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            ["paplay", str(path)],
            ["aplay", "-q", str(path)],
            ["afplay", str(path)],
        ):
            if shutil.which(player[0]):
                try:
                    subprocess.run(player, check=True, capture_output=True, timeout=120)
                    return True
                except (subprocess.SubprocessError, OSError) as exc:
                    logger.debug("%s playback failed: %s", player[0], exc)
        return False

    return await asyncio.to_thread(_play_blocking)


class PiperEngine(TTSEngineBase):
    """Offline neural TTS via the piper binary + a downloaded voice model."""

    name = "piper"

    def _model_path(self) -> Optional[Path]:
        models = paths.models_dir() / "piper"
        if not models.exists():
            return None
        candidates = sorted(models.glob("*.onnx"))
        return candidates[0] if candidates else None

    def available(self) -> bool:
        return has_binary("piper") and self._model_path() is not None

    async def synthesize(self, text: str) -> Optional[Path]:
        model = self._model_path()
        out = _out_path(".wav")

        def _run() -> Optional[Path]:
            try:
                subprocess.run(
                    ["piper", "--model", str(model), "--output_file", str(out)],
                    input=text.encode("utf-8"),
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                return out if out.exists() else None
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("piper failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)


class Pyttsx3Engine(TTSEngineBase):
    """Offline OS voices via pyttsx3."""

    name = "pyttsx3"

    def available(self) -> bool:
        return try_import("pyttsx3") is not None

    async def synthesize(self, text: str) -> Optional[Path]:
        pyttsx3 = try_import("pyttsx3")
        if pyttsx3 is None:
            return None
        out = _out_path(".wav")

        def _run() -> Optional[Path]:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", settings.TTS_RATE)
                engine.setProperty("volume", settings.TTS_VOLUME)
                if settings.TTS_VOICE:
                    for voice in engine.getProperty("voices"):
                        if settings.TTS_VOICE.lower() in (voice.name or "").lower():
                            engine.setProperty("voice", voice.id)
                            break
                engine.save_to_file(text, str(out))
                engine.runAndWait()
                return out if out.exists() else None
            except Exception as exc:  # noqa: BLE001 - driver init can fail oddly
                logger.debug("pyttsx3 failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)

    async def speak(self, text: str) -> bool:
        pyttsx3 = try_import("pyttsx3")
        if pyttsx3 is None:
            return False

        def _run() -> bool:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", settings.TTS_RATE)
                engine.setProperty("volume", settings.TTS_VOLUME)
                engine.say(text)
                engine.runAndWait()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("pyttsx3 speak failed: %s", exc)
                return False

        return await asyncio.to_thread(_run)


class EspeakEngine(TTSEngineBase):
    """Direct espeak-ng / espeak binary (also 'say' on macOS)."""

    name = "espeak"

    def _binary(self) -> Optional[str]:
        for candidate in ("espeak-ng", "espeak"):
            if has_binary(candidate):
                return candidate
        if is_macos() and has_binary("say"):
            return "say"
        return None

    def available(self) -> bool:
        return self._binary() is not None

    async def synthesize(self, text: str) -> Optional[Path]:
        binary = self._binary()
        if binary is None:
            return None
        out = _out_path(".wav")

        def _run() -> Optional[Path]:
            try:
                if binary == "say":
                    subprocess.run(["say", "-o", str(out), "--data-format=LEF32@22050", text],
                                   check=True, capture_output=True, timeout=60)
                else:
                    subprocess.run([binary, "-s", str(settings.TTS_RATE), "-w", str(out), text],
                                   check=True, capture_output=True, timeout=60)
                return out if out.exists() else None
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("%s failed: %s", binary, exc)
                return None

        return await asyncio.to_thread(_run)


class EdgeTTSEngine(TTSEngineBase):
    """Free Microsoft Edge neural voices (needs network)."""

    name = "edge"
    DEFAULT_VOICE = "en-US-AriaNeural"

    def available(self) -> bool:
        return try_import("edge_tts") is not None

    async def synthesize(self, text: str) -> Optional[Path]:
        edge_tts = try_import("edge_tts")
        if edge_tts is None:
            return None
        out = _out_path(".mp3")
        voice = settings.TTS_VOICE or self.DEFAULT_VOICE
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(out))
            return out if out.exists() else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("edge-tts failed: %s", exc)
            return None


class GTTSEngine(TTSEngineBase):
    """Google Translate voices via gTTS (needs network)."""

    name = "gtts"

    def available(self) -> bool:
        return try_import("gtts") is not None

    async def synthesize(self, text: str) -> Optional[Path]:
        gtts = try_import("gtts")
        if gtts is None:
            return None
        out = _out_path(".mp3")

        def _run() -> Optional[Path]:
            try:
                gtts.gTTS(text=text, lang="en").save(str(out))
                return out if out.exists() else None
            except Exception as exc:  # noqa: BLE001
                logger.debug("gTTS failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)


ENGINE_CLASSES: dict[str, type[TTSEngineBase]] = {
    "piper": PiperEngine,
    "pyttsx3": Pyttsx3Engine,
    "espeak": EspeakEngine,
    "edge": EdgeTTSEngine,
    "gtts": GTTSEngine,
}

_AUTO_ORDER = ("piper", "pyttsx3", "espeak", "edge", "gtts")


def pick_engine() -> Optional[TTSEngineBase]:
    """Choose the best available engine per configuration."""
    wanted = settings.TTS_ENGINE.lower()
    if wanted == "browser":
        return None
    if wanted != "auto":
        cls = ENGINE_CLASSES.get(wanted)
        if cls is not None:
            engine = cls()
            if engine.available():
                return engine
            logger.warning("Configured TTS engine %r unavailable; falling back to auto.", wanted)
    for name in _AUTO_ORDER:
        engine = ENGINE_CLASSES[name]()
        if engine.available():
            return engine
    return None
