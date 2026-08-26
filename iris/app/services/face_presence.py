"""Keeps the robot's OLED eyes in step with what IRIS is doing.

Without this, the face would only ever change when someone explicitly asked
it to — which is not a face, it is a status light. This service watches the
event bus and reacts:

* IRIS starts speaking  -> an expression inferred from the sentence, plus the
  talking animation for as long as the sentence should take to say
* the wake word fires   -> a listening face
* IRIS starts thinking  -> a thinking face

Three properties matter more than the feature itself:

**It never delays the voice.** Every push is a fire-and-forget task with a
short timeout, so a face node that has been unplugged cannot make IRIS pause
before it speaks.

**It never strands the animation.** The duration is sent up front and the
firmware expires it on its own. Nothing here has to successfully deliver a
"stopped speaking" message — the same reasoning as the robot base auto-stopping
rather than trusting a stop command to arrive.

**It goes quiet when there is no face.** An unreachable node backs off instead
of logging once per sentence forever.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from iris.app.core.bus import EventBus, Subscription, Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.tools.devices.face import estimate_speech_ms, infer_emotion, push_face
from iris.app.tools.devices.registry import DeviceRegistry, default_device_registry

logger = get_logger("services.face")

#: A face node answers in milliseconds on a healthy LAN. Anything slower is not
#: worth making the voice wait for, so the push is abandoned.
PUSH_TIMEOUT_S = 2.0

#: After this many consecutive failures, stop trying for BACKOFF_S.
FAILURES_BEFORE_BACKOFF = 3
BACKOFF_S = 60.0

#: The same sentence can be published twice — once for the server voice and
#: again if it falls back to the browser. Ignore the echo.
DEDUPE_WINDOW_S = 1.5


class FacePresenceService:
    """Mirrors IRIS's state onto a registered ``face`` device."""

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        registry: Optional[DeviceRegistry] = None,
    ):
        self._bus = bus or default_event_bus
        self._registry = registry or default_device_registry
        self._sub: Optional[Subscription] = None
        self._task: Optional[asyncio.Task] = None
        self._pending: set[asyncio.Task] = set()
        self._failures = 0
        self._quiet_until = 0.0
        self._last_text = ""
        self._last_text_at = 0.0

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._task is not None:
            return
        self._sub = self._bus.subscribe(
            [Topics.VOICE_SPEAKING, Topics.VOICE_WAKE, Topics.AGENT_THINKING]
        )
        self._task = asyncio.create_task(self._run(), name="face-presence")
        logger.info("Face expression service started.")

    async def stop(self) -> None:
        if self._sub is not None:
            self._bus.unsubscribe(self._sub)
            self._sub = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        for task in list(self._pending):
            task.cancel()
        self._pending.clear()

    # ------------------------------------------------------------------ loop
    async def _run(self) -> None:
        assert self._sub is not None
        try:
            async for event in self._sub:
                try:
                    self._handle(event.topic, event.payload or {})
                except Exception as exc:  # noqa: BLE001 - a face must never break the voice
                    logger.debug("Face reaction skipped: %s", exc)
        except asyncio.CancelledError:
            raise

    def _handle(self, topic: str, payload: dict) -> None:
        if topic == Topics.VOICE_SPEAKING:
            text = str(payload.get("text") or "")
            if not text.strip() or self._is_echo(text):
                return
            self._dispatch(
                emotion=infer_emotion(text),
                speak_ms=estimate_speech_ms(text),
            )
        elif topic == Topics.VOICE_WAKE:
            self._dispatch(emotion="listening", speak_ms=0)
        elif topic == Topics.AGENT_THINKING:
            self._dispatch(emotion="thinking", speak_ms=0)

    def _is_echo(self, text: str) -> bool:
        now = time.monotonic()
        repeat = text == self._last_text and (now - self._last_text_at) < DEDUPE_WINDOW_S
        self._last_text = text
        self._last_text_at = now
        return repeat

    # ------------------------------------------------------------- dispatch
    def _dispatch(self, *, emotion: str, speak_ms: int) -> None:
        """Queue a push without ever awaiting it on the caller's path."""
        if not settings.FACE_AUTO_EXPRESSION:
            return
        if time.monotonic() < self._quiet_until:
            return
        device = self._registry.first_of_kind("face")
        if device is None:
            return
        task = asyncio.create_task(self._push(device.base_url, emotion, speak_ms))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _push(self, base_url: str, emotion: str, speak_ms: int) -> None:
        try:
            await asyncio.wait_for(
                push_face(base_url, emotion=emotion, speak_ms=speak_ms or None),
                timeout=PUSH_TIMEOUT_S,
            )
            self._failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the node being off is normal
            self._failures += 1
            if self._failures == FAILURES_BEFORE_BACKOFF:
                self._quiet_until = time.monotonic() + BACKOFF_S
                logger.info(
                    "Face node unreachable (%s) — pausing expressions for %.0fs.",
                    exc, BACKOFF_S,
                )
                self._failures = 0
            else:
                logger.debug("Face push failed: %s", exc)


default_face_presence_service = FacePresenceService()
