"""Speech-to-text engines with layered fallbacks.

Engine order (configurable via ``STT_ENGINE``):

1. **faster_whisper** — best offline accuracy (CTranslate2 Whisper).
2. **vosk** — light offline recognition with small downloadable models.
3. **google_free** — the free Google Web Speech API via ``SpeechRecognition``
   (needs network, no key).
4. **browser** — recognition happens in the web UI with the browser's own
   ``webkitSpeechRecognition``; the server just receives final text. Always
   available, needs nothing installed.

Uploaded audio arrives as WAV/WebM/OGG; non-WAV containers are converted with
ffmpeg when present.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import has_binary, try_import

logger = get_logger("voice.stt")


class STTResult:
    """Transcription output."""

    def __init__(self, text: str, engine: str, language: str = "", confidence: float = 0.0):
        self.text = text
        self.engine = engine
        self.language = language
        self.confidence = confidence

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "engine": self.engine,
            "language": self.language,
            "confidence": self.confidence,
        }


def _ensure_wav(audio_bytes: bytes, content_type: str = "") -> Optional[bytes]:
    """Convert arbitrary uploaded audio to 16k mono WAV via ffmpeg when needed."""
    if audio_bytes[:4] == b"RIFF":
        return audio_bytes
    if not has_binary("ffmpeg"):
        logger.warning("Non-WAV audio (%s) and ffmpeg missing; cannot convert.", content_type or "unknown")
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as src:
            src.write(audio_bytes)
            src_path = src.name
        dst_path = src_path + ".wav"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "quiet", "-i", src_path,
             "-ar", str(settings.MIC_SAMPLE_RATE), "-ac", "1", dst_path],
            check=True, capture_output=True, timeout=60,
        )
        data = Path(dst_path).read_bytes()
        Path(src_path).unlink(missing_ok=True)
        Path(dst_path).unlink(missing_ok=True)
        return data
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("ffmpeg conversion failed: %s", exc)
        return None


class STTEngineBase:
    """Interface for one STT backend."""

    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    async def transcribe(self, wav_bytes: bytes) -> Optional[STTResult]:
        raise NotImplementedError


class FasterWhisperEngine(STTEngineBase):
    """Offline Whisper via CTranslate2."""

    name = "faster_whisper"
    _model_cache: dict[str, object] = {}

    def available(self) -> bool:
        return try_import("faster_whisper") is not None

    def _get_model(self):
        fw = try_import("faster_whisper")
        model_name = settings.STT_MODEL or "base.en"
        if model_name not in self._model_cache:
            self._model_cache[model_name] = fw.WhisperModel(
                model_name,
                device="auto",
                compute_type="int8",
                download_root=str(paths.models_dir() / "whisper"),
            )
        return self._model_cache[model_name]

    async def transcribe(self, wav_bytes: bytes) -> Optional[STTResult]:
        def _run() -> Optional[STTResult]:
            try:
                model = self._get_model()
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(wav_bytes)
                    tmp_path = tmp.name
                language = None if settings.STT_LANGUAGE == "auto" else settings.STT_LANGUAGE
                segments, info = model.transcribe(tmp_path, language=language, beam_size=3)
                text = " ".join(segment.text.strip() for segment in segments).strip()
                Path(tmp_path).unlink(missing_ok=True)
                if not text:
                    return None
                return STTResult(
                    text=text,
                    engine=self.name,
                    language=getattr(info, "language", "") or "",
                    confidence=float(getattr(info, "language_probability", 0.0) or 0.0),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("faster-whisper failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)


class VoskEngine(STTEngineBase):
    """Light offline recognition via Vosk."""

    name = "vosk"
    _model = None

    def _model_dir(self) -> Optional[Path]:
        base = paths.models_dir() / "vosk"
        if not base.exists():
            return None
        dirs = [d for d in base.iterdir() if d.is_dir()]
        return dirs[0] if dirs else None

    def available(self) -> bool:
        return try_import("vosk") is not None and self._model_dir() is not None

    async def transcribe(self, wav_bytes: bytes) -> Optional[STTResult]:
        vosk = try_import("vosk")
        model_dir = self._model_dir()
        if vosk is None or model_dir is None:
            return None

        def _run() -> Optional[STTResult]:
            try:
                if VoskEngine._model is None:
                    vosk.SetLogLevel(-1)
                    VoskEngine._model = vosk.Model(str(model_dir))
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(wav_bytes)
                    tmp_path = tmp.name
                with wave.open(tmp_path, "rb") as wf:
                    recognizer = vosk.KaldiRecognizer(VoskEngine._model, wf.getframerate())
                    while True:
                        chunk = wf.readframes(4000)
                        if not chunk:
                            break
                        recognizer.AcceptWaveform(chunk)
                    payload = json.loads(recognizer.FinalResult())
                Path(tmp_path).unlink(missing_ok=True)
                text = (payload.get("text") or "").strip()
                return STTResult(text=text, engine=self.name) if text else None
            except Exception as exc:  # noqa: BLE001
                logger.warning("vosk failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)


class GoogleFreeEngine(STTEngineBase):
    """Free Google Web Speech API through the SpeechRecognition package."""

    name = "google_free"

    def available(self) -> bool:
        return try_import("speech_recognition") is not None

    async def transcribe(self, wav_bytes: bytes) -> Optional[STTResult]:
        sr = try_import("speech_recognition")
        if sr is None:
            return None

        def _run() -> Optional[STTResult]:
            try:
                recognizer = sr.Recognizer()
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(wav_bytes)
                    tmp_path = tmp.name
                with sr.AudioFile(tmp_path) as source:
                    audio = recognizer.record(source)
                Path(tmp_path).unlink(missing_ok=True)
                language = "en-US" if settings.STT_LANGUAGE == "auto" else settings.STT_LANGUAGE
                text = recognizer.recognize_google(audio, language=language)
                return STTResult(text=text, engine=self.name) if text else None
            except Exception as exc:  # noqa: BLE001
                logger.warning("google_free STT failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)


ENGINE_CLASSES: dict[str, type[STTEngineBase]] = {
    "faster_whisper": FasterWhisperEngine,
    "vosk": VoskEngine,
    "google_free": GoogleFreeEngine,
}

_AUTO_ORDER = ("faster_whisper", "vosk", "google_free")


def pick_engine() -> Optional[STTEngineBase]:
    """Choose the best available STT engine per configuration."""
    wanted = settings.STT_ENGINE.lower()
    if wanted == "browser":
        return None
    if wanted != "auto":
        cls = ENGINE_CLASSES.get(wanted)
        if cls is not None:
            engine = cls()
            if engine.available():
                return engine
            logger.warning("Configured STT engine %r unavailable; falling back to auto.", wanted)
    for name in _AUTO_ORDER:
        engine = ENGINE_CLASSES[name]()
        if engine.available():
            return engine
    return None


async def transcribe_upload(audio_bytes: bytes, content_type: str = "") -> Optional[STTResult]:
    """Transcribe an uploaded audio blob using the best available engine."""
    wav = _ensure_wav(audio_bytes, content_type)
    if wav is None:
        return None
    engine = pick_engine()
    if engine is None:
        return None
    return await engine.transcribe(wav)
