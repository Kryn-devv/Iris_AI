"""Endpoints for nodes that dial in to IRIS.

Everything else in the API is a person or a UI talking to IRIS. These are for
the *hardware*: an ESP32 opening an outbound WebSocket so a cloud-hosted IRIS
can reach it through a home router, and a voice round-trip so a microphone and
speaker on that board become IRIS's ears and mouth.

Both are authenticated with a shared token. A command channel that can switch
mains relays and drive motors must never be open to whoever finds the port.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from iris.app.agent.kernel import default_kernel
from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.nodes.link import NodeLink, default_node_hub
from iris.app.tools.devices.registry import (
    DEVICE_KINDS,
    Device,
    DeviceError,
    default_device_registry,
    normalize_name,
)
from iris.app.voice.service import default_voice_service
from iris.app.voice.tts import ensure_wav

router = APIRouter(prefix="/api/v1/nodes", tags=["Nodes"])
logger = get_logger("api.nodes")

#: Sent when nothing has arrived for this long, to keep NAT mappings alive and
#: to notice a half-open socket that still looks fine from our side.
PING_EVERY_S = 25.0

#: Raw audio limits. 16 kHz mono 16-bit is 32 kB per second, so this is about
#: 30 seconds — far more than any spoken command.
MAX_AUDIO_BYTES = 1_000_000
MIN_AUDIO_BYTES = 2_000          # under ~60 ms there is nothing to transcribe
ALLOWED_RATES = (8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000)


def _require_token(supplied: Optional[str]) -> None:
    """Refuse unless a configured token matches.

    With no token configured the answer is a refusal, not a free pass: this
    endpoint can switch mains relays and drive a robot, and it is reachable
    from the internet whenever IRIS is hosted anywhere but a home LAN.
    """
    expected = (settings.NODE_LINK_TOKEN or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Node links are not configured. Set NODE_LINK_TOKEN in .env "
                   "to a long random string and put the same value in the firmware.",
        )
    if not supplied or supplied.strip() != expected:
        raise HTTPException(status_code=401, detail="Bad node token.")


# --------------------------------------------------------------------- status
@router.get("", summary="Nodes currently linked to IRIS")
async def list_nodes() -> Dict[str, Any]:
    """Which boards are dialled in right now, and their latest readings."""
    return {
        "count": default_node_hub.count,
        "nodes": default_node_hub.info(),
        "link_configured": bool((settings.NODE_LINK_TOKEN or "").strip()),
    }


# ----------------------------------------------------------------- the socket
@router.websocket("/link")
async def node_link(
    websocket: WebSocket,
    token: str = Query(default=""),
    name: str = Query(default=""),
    kind: str = Query(default="generic"),
) -> None:
    """A node dials in and holds this open; commands travel back down it.

    The node initiates, so this works from behind any home router with no
    port-forwarding, no static IP and nothing exposed to the internet.
    """
    expected = (settings.NODE_LINK_TOKEN or "").strip()
    if not settings.NODE_LINK_ENABLED or not expected:
        await websocket.close(code=1008, reason="node links not configured")
        return
    if token.strip() != expected:
        # Closed before accept, so an attacker learns nothing about the name.
        await websocket.close(code=1008, reason="bad token")
        return
    try:
        node_name = normalize_name(name)
    except DeviceError:
        await websocket.close(code=1008, reason="bad node name")
        return
    node_kind = kind if kind in DEVICE_KINDS else "generic"

    await websocket.accept()

    # Starlette's send is not safe to call from two coroutines at once, and a
    # command being written while the pinger fires would interleave frames.
    send_lock = asyncio.Lock()

    async def send(frame: str) -> None:
        async with send_lock:
            await websocket.send_text(frame)

    client = websocket.client
    link = NodeLink(
        name=node_name,
        kind=node_kind,
        send=send,
        remote=f"{client.host}:{client.port}" if client else "",
    )
    previous = default_node_hub.register(link)
    if previous is not None and previous is not link:
        previous.close("replaced by a new connection")

    # Registering the device means "robot forward" works without the user
    # having to type an address that does not exist for a linked node.
    _ensure_registered(node_name, node_kind)

    async def pinger() -> None:
        while True:
            await asyncio.sleep(PING_EVERY_S)
            try:
                await send('{"type":"ping"}')
            except Exception:  # noqa: BLE001 - socket gone; the reader will see it
                return
            if link.stale:
                logger.info("Node '%s' went quiet — closing", link.name)
                return

    ping_task = asyncio.create_task(pinger(), name=f"node-ping-{node_name}")
    reason = "closed"
    try:
        while True:
            raw = await websocket.receive_text()
            reply = default_node_hub.handle_message(link, raw)
            if reply is not None:
                await send(json.dumps(reply))
            if ping_task.done():        # the pinger decided the node is stale
                reason = "stale"
                break
    except WebSocketDisconnect:
        reason = "disconnected"
    except Exception as exc:  # noqa: BLE001
        reason = f"error: {exc}"
        logger.debug("Node '%s' link error: %s", node_name, exc)
    finally:
        ping_task.cancel()
        default_node_hub.unregister(link, reason)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already gone
            pass


def _ensure_registered(name: str, kind: str) -> None:
    """Make a linked node a registered device, without clobbering a LAN one."""
    existing = default_device_registry.get(name)
    if existing is not None and not existing.linked:
        return          # the user configured this one by address; leave it be
    if existing is not None and existing.kind == kind:
        return
    try:
        default_device_registry.add(Device(name=name, kind=kind, transport="link"))
    except DeviceError as exc:
        logger.warning("Could not register linked node '%s': %s", name, exc)


# ------------------------------------------------------------------ the voice
def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Wrap raw 16-bit PCM in a WAV container.

    The node streams raw samples because a WAV header needs the total length up
    front, which a device recording and uploading at the same time does not
    know. The header is added here instead, where the length is known.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


@router.post("/voice", summary="Node voice round-trip: mic audio in, speaker audio out")
async def node_voice(
    request: Request,
    token: str = Query(default=""),
    node: str = Query(default=""),
    rate: int = Query(default=16000),
    conversation_id: Optional[str] = Query(default=None),
) -> FileResponse:
    """Take a recording from a node's microphone and answer with audio.

    One request does the whole loop: transcribe, run the agent, speak the
    reply. The node uploads raw PCM as it records and plays the WAV that comes
    back, so it needs no audio codec and no second round trip.
    """
    _require_token(token)
    if not settings.NODE_VOICE_ENABLED:
        raise HTTPException(status_code=503, detail="Node voice is disabled.")
    if rate not in ALLOWED_RATES:
        raise HTTPException(
            status_code=400,
            detail=f"rate must be one of {', '.join(str(r) for r in ALLOWED_RATES)}.",
        )

    pcm = await request.body()
    if len(pcm) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Recording too long.")
    if len(pcm) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="Recording too short to transcribe.")
    if len(pcm) % 2:
        pcm = pcm[:-1]          # a truncated final sample, not an error

    started = time.monotonic()
    transcript = await default_voice_service.transcribe(pcm_to_wav(pcm, rate), "audio/wav")
    if transcript is None:
        raise HTTPException(
            status_code=503,
            detail="No speech-to-text engine is installed on the server. "
                   "pip install faster-whisper (or vosk) and restart.",
        )
    text = (transcript.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Could not make out any speech.")

    logger.info("Node '%s' said: %s", node or "?", text)
    default_event_bus.publish(
        Topics.VOICE_FINAL, {"text": text, "source": f"node:{node}" if node else "node"}
    )

    reply = await default_kernel.process_request(
        user_input=text, conversation_id=conversation_id, channel="voice"
    )
    sentence = (reply.speech or reply.response or "").strip()
    if not sentence:
        raise HTTPException(status_code=500, detail="The agent produced no reply to speak.")

    # Published before synthesis so the OLED eyes start reacting while the
    # audio is still being generated, rather than a beat behind the speaker.
    default_event_bus.publish(
        Topics.VOICE_SPEAKING,
        {"text": sentence, "engine": "node", "language": "en", "node": node},
    )

    audio_path = await default_voice_service.synthesize(sentence)
    if audio_path is None:
        raise HTTPException(
            status_code=503,
            detail="No text-to-speech engine is installed on the server. "
                   "Install piper (recommended) so nodes can speak.",
        )
    wav = ensure_wav(Path(audio_path))
    if wav is None:
        raise HTTPException(
            status_code=503,
            detail="The TTS engine produced audio a node cannot play. Install piper "
                   "for WAV output, or install ffmpeg so mp3 can be converted.",
        )

    logger.info("Node '%s' round trip in %.2fs", node or "?", time.monotonic() - started)
    return FileResponse(
        wav,
        media_type="audio/wav",
        headers={
            # Handy when watching a node from a serial monitor, and harmless.
            "X-Iris-Heard": text[:200].encode("ascii", "replace").decode("ascii"),
            "X-Iris-Reply": sentence[:200].encode("ascii", "replace").decode("ascii"),
        },
    )
