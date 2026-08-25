"""Voice endpoints: transcription, speech synthesis and voice commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from iris.app.agent.kernel import default_kernel
from iris.app.core.logging import get_logger
from iris.app.schemas.messages import ChatResponse
from iris.app.voice.service import default_voice_service

router = APIRouter(prefix="/api/v1/voice", tags=["Voice"])
logger = get_logger("api.voice")

_MAX_AUDIO_BYTES = 25_000_000


@router.get("/status", summary="Voice pipeline status")
async def voice_status() -> Dict[str, Any]:
    """Which STT/TTS engines are active, wake words, browser-voice mode."""
    return default_voice_service.status()


class SpeakRequest(BaseModel):
    text: str


@router.post("/speak", summary="Speak text on the host machine")
async def speak(request: SpeakRequest) -> Dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Nothing to say.")
    return await default_voice_service.speak(request.text)


@router.post("/synthesize", summary="Synthesize speech and return the audio file")
async def synthesize(request: SpeakRequest) -> FileResponse:
    """Produce an audio file (for phone clients that play audio themselves)."""
    path = await default_voice_service.synthesize(request.text)
    if path is None:
        raise HTTPException(
            status_code=503,
            detail="No server TTS engine available — use the browser's speechSynthesis instead.",
        )
    media_type = "audio/mpeg" if path.endswith(".mp3") else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=Path(path).name)


@router.post("/transcribe", summary="Transcribe uploaded audio to text")
async def transcribe(audio: UploadFile = File(...)) -> Dict[str, Any]:
    data = await audio.read()
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large (max 25 MB).")
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio upload.")
    result = await default_voice_service.transcribe(data, audio.content_type or "")
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No server STT engine available — use the browser's speech recognition instead.",
        )
    return result


@router.post("/command", response_model=ChatResponse, summary="Full voice round-trip: audio in, action out")
async def voice_command(
    audio: UploadFile = File(...),
    conversation_id: Optional[str] = None,
) -> ChatResponse:
    """Transcribe audio, run it through the kernel and return the response."""
    data = await audio.read()
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large (max 25 MB).")
    result = await default_voice_service.transcribe(data, audio.content_type or "")
    if result is None or not result.get("text"):
        raise HTTPException(status_code=422, detail="Could not understand the audio.")
    return await default_kernel.process_request(
        user_input=result["text"],
        conversation_id=conversation_id,
        channel="voice",
    )
