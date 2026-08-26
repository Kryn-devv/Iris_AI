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
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import has_binary, is_macos, is_windows, try_import

logger = get_logger("voice.tts")

# Name fragments that mark female OS voices, in rough preference order.
_FEMALE_HINDI_HINTS = ("heera", "kalpana", "lekha", "swara", "hindi female")
_FEMALE_HINTS = ("zira", "aria", "jenny", "samantha", "susan", "hazel", "eva", "female")


def pick_pyttsx3_voice(voices, language: str = "en") -> Optional[str]:
    """Pick a voice id from a pyttsx3 voice list.

    Preference order: the configured ``TTS_VOICE`` name, a female voice for the
    requested language (Hindi voices for ``hi``/``hinglish``), then any female
    voice. Returns ``None`` when nothing matches (keep the OS default voice).
    """
    def _name(voice) -> str:
        return (getattr(voice, "name", "") or "").lower()

    if settings.TTS_VOICE:
        wanted = settings.TTS_VOICE.lower()
        for voice in voices:
            if wanted in _name(voice):
                return voice.id
    hints = _FEMALE_HINTS
    if (language or "").lower().startswith("hi"):
        hints = _FEMALE_HINDI_HINTS + _FEMALE_HINTS
    for hint in hints:
        for voice in voices:
            if hint in _name(voice):
                return voice.id
    return None


class TTSEngineBase:
    """Interface for one TTS backend."""

    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    async def synthesize(self, text: str, language: str = "en") -> Optional[Path]:
        """Produce an audio file for ``text`` (None when engine plays directly)."""
        raise NotImplementedError

    async def speak(self, text: str, language: str = "en") -> bool:
        """Speak ``text`` on the server's speakers. Returns success."""
        path = await self.synthesize(text, language)
        if path is None:
            return False
        return await play_audio_file(path)


def _out_path(suffix: str) -> Path:
    directory = paths.recordings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"tts_{int(time.time())}_{uuid.uuid4().hex[:6]}{suffix}"


def _play_windows_blocking(path: Path) -> bool:
    """Windows playback: SoundPlayer for wav, ffmpeg→wav for mp3, startfile last."""
    def _soundplayer(wav: Path) -> bool:
        try:
            subprocess.run(
                ["powershell", "-c", f"(New-Object Media.SoundPlayer '{wav}').PlaySync()"],
                check=True, capture_output=True, timeout=120,
            )
            return True
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("SoundPlayer playback failed: %s", exc)
            return False

    suffix = path.suffix.lower()
    if suffix == ".wav" and _soundplayer(path):
        return True
    if suffix == ".mp3" and shutil.which("ffmpeg"):
        wav = path.with_suffix(".wav")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "quiet", "-i", str(path), str(wav)],
                check=True, capture_output=True, timeout=60,
            )
            if wav.exists() and _soundplayer(wav):
                return True
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("ffmpeg conversion failed: %s", exc)
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # default app, last resort
        return True
    except (AttributeError, OSError) as exc:
        logger.debug("os.startfile failed: %s", exc)
        return False


def ensure_wav(path: Path) -> Optional[Path]:
    """Return a 16-bit PCM WAV for ``path``, converting an mp3 when possible.

    An ESP32 has no mp3 decoder, so audio bound for a speaker on a node has to
    be WAV. The device reads the sample rate out of the header and configures
    I2S to match, which is why no resampling happens here — whatever rate the
    engine produced is fine.
    """
    if path.suffix.lower() == ".wav":
        return path if path.exists() else None
    if path.suffix.lower() == ".mp3" and shutil.which("ffmpeg"):
        wav = path.with_suffix(".wav")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "quiet", "-i", str(path),
                 "-acodec", "pcm_s16le", str(wav)],
                check=True, capture_output=True, timeout=60,
            )
            if wav.exists():
                return wav
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("ffmpeg conversion failed: %s", exc)
    return None


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
        if is_windows():
            return _play_windows_blocking(path)
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

    def _model_path(self, language: str = "en") -> Optional[Path]:
        models = paths.models_dir() / "piper"
        if not models.exists():
            return None
        candidates = sorted(models.glob("*.onnx"))
        if not candidates:
            return None
        if settings.TTS_VOICE:
            wanted = settings.TTS_VOICE.lower()
            for candidate in candidates:
                if wanted in candidate.name.lower():
                    return candidate
        if (language or "").lower().startswith("hi"):
            for candidate in candidates:
                if "hi" in candidate.name.lower():
                    return candidate
        return candidates[0]

    def available(self) -> bool:
        return has_binary("piper") and self._model_path() is not None

    async def synthesize(self, text: str, language: str = "en") -> Optional[Path]:
        model = self._model_path(language)
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

    async def synthesize(self, text: str, language: str = "en") -> Optional[Path]:
        pyttsx3 = try_import("pyttsx3")
        if pyttsx3 is None:
            return None
        out = _out_path(".wav")

        def _run() -> Optional[Path]:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", settings.TTS_RATE)
                engine.setProperty("volume", settings.TTS_VOLUME)
                voice_id = pick_pyttsx3_voice(engine.getProperty("voices") or [], language)
                if voice_id:
                    engine.setProperty("voice", voice_id)
                engine.save_to_file(text, str(out))
                engine.runAndWait()
                return out if out.exists() else None
            except Exception as exc:  # noqa: BLE001 - driver init can fail oddly
                logger.debug("pyttsx3 failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)

    async def speak(self, text: str, language: str = "en") -> bool:
        pyttsx3 = try_import("pyttsx3")
        if pyttsx3 is None:
            return False

        def _run() -> bool:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", settings.TTS_RATE)
                engine.setProperty("volume", settings.TTS_VOLUME)
                voice_id = pick_pyttsx3_voice(engine.getProperty("voices") or [], language)
                if voice_id:
                    engine.setProperty("voice", voice_id)
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

    async def synthesize(self, text: str, language: str = "en") -> Optional[Path]:
        binary = self._binary()
        if binary is None:
            return None
        out = _out_path(".wav")
        lang = (language or "en").lower()

        def _run() -> Optional[Path]:
            try:
                if binary == "say":
                    voice = settings.TTS_VOICE or ("Lekha" if lang.startswith("hi") else "Samantha")
                    subprocess.run(["say", "-v", voice, "-o", str(out), "--data-format=LEF32@22050", text],
                                   check=True, capture_output=True, timeout=60)
                else:
                    espeak_voice = "hi+f3" if lang.startswith("hi") else "en+f3"  # f3 = female variant
                    subprocess.run([binary, "-v", espeak_voice, "-s", str(settings.TTS_RATE), "-w", str(out), text],
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
    # Female neural voices per language; hinglish speaks best with a Hindi voice.
    VOICE_BY_LANGUAGE = {
        "en": "en-US-AriaNeural",
        "hi": "hi-IN-SwaraNeural",
        "hinglish": "hi-IN-SwaraNeural",
    }

    def _voice_for(self, language: str) -> str:
        if settings.TTS_VOICE:
            return settings.TTS_VOICE
        lang = (language or "en").lower()
        if lang in self.VOICE_BY_LANGUAGE:
            return self.VOICE_BY_LANGUAGE[lang]
        return self.VOICE_BY_LANGUAGE.get(lang.split("-")[0], self.DEFAULT_VOICE)

    def available(self) -> bool:
        return try_import("edge_tts") is not None

    async def synthesize(self, text: str, language: str = "en") -> Optional[Path]:
        edge_tts = try_import("edge_tts")
        if edge_tts is None:
            return None
        out = _out_path(".mp3")
        voice = self._voice_for(language)
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

    async def synthesize(self, text: str, language: str = "en") -> Optional[Path]:
        gtts = try_import("gtts")
        if gtts is None:
            return None
        out = _out_path(".mp3")
        lang = "hi" if (language or "").lower().startswith("hi") else "en"

        def _run() -> Optional[Path]:
            try:
                gtts.gTTS(text=text, lang=lang).save(str(out))
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
