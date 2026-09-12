"""What the camera does when nobody is asking.

The camera tools answer questions — "who am I", "what is this". A robot that
only ever looks when told to is a webcam with a voice. This service makes the
eye *attentive*: it keeps asking the camera the cheap question ("has anything
moved?"), and only when the answer is yes does it fetch a frame and think.

* someone it knows walks in  -> "Good evening, Prakash." — once, then not
  again for a while, however long they stand there
* someone it does not know   -> "Someone I don't recognise is here."
* something is set down in front of it and stops moving -> the vision model
  names it: "I see a red apple." — and not again for the same thing

Three properties matter more than the feature:

**It is cheap when nothing happens.** One small JSON request per second to
the camera's own motion detector; no frame leaves the camera until something
moves, and no vision-model call is made for an empty room.

**It never repeats itself.** Every announcement has a cooldown and the object
announcement is also compared with the previous one, so a person standing
still or a flickering shadow cannot turn into a voice on a loop.

**It goes quiet when the camera is off.** Three failures in a row and it stops
trying for a minute, logging once — the same rule the face service uses.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from iris.app.core.bus import EventBus, Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.tools.devices.registry import Device, DeviceRegistry, default_device_registry
from iris.app.vision.robot_eye import (
    FaceRecognizer,
    FaceStore,
    Sighting,
    VisionUnavailable,
    look,
    module_available,
    select_backend,
)
from iris.app.vision.robot_eye.camera import FACE_FRAME_SIZE
from iris.app.vision.robot_eye.describe import build_vision_messages, shape_answer

logger = get_logger("services.camera_watch")

#: With no camera registered, look for one this often.
NO_CAMERA_POLL_S = 10.0
#: Frames are never fetched closer together than this, whatever the motion says.
LOOK_COOLDOWN_S = 2.0
#: A camera whose firmware has no motion detector is looked at this often.
BLIND_LOOK_INTERVAL_S = 6.0
#: After this many consecutive failures, stop trying for BACKOFF_S.
FAILURES_BEFORE_BACKOFF = 3
BACKOFF_S = 60.0
#: Key under which "a stranger" shares the greeting cooldown table.
STRANGER_KEY = "?"
#: The eyes hold the reaction this long.
FACE_HOLD_MS = 5000

#: What the vision model is asked when something was set down. It must be
#: allowed to say "nothing": a person walking out of frame also counts as
#: motion that stopped, and an empty room is not worth announcing.
OBJECT_WATCH_PROMPT = (
    "You are the eyes of a small desk robot. Someone may have just placed an object "
    "in front of you. If there is a clear object in the foreground, say what it is in "
    "one short sentence starting with 'I see'. If there is nothing notable — only a "
    "room, a wall, a floor, a person, or a hand — answer with the single word NOTHING."
)
NOTHING = "nothing"


def greeting_for(name: str, *, owner: bool, hour: int) -> str:
    """The sentence for a recognised person. Pure, so it can be tested."""
    if hour < 5:
        opener = "You're up late"
    elif hour < 12:
        opener = "Good morning"
    elif hour < 17:
        opener = "Good afternoon"
    else:
        opener = "Good evening"
    if owner:
        return f"{opener}, {name}. Good to see you."
    return f"{opener}, {name}."


class CameraWatchService:
    """Watches through a registered ``camera`` device and speaks up on its own."""

    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
        bus: Optional[EventBus] = None,
        store: Optional[FaceStore] = None,
        *,
        recognizer_factory: Optional[Callable[[], Optional[FaceRecognizer]]] = None,
        gateway_factory: Optional[Callable[[], Any]] = None,
        speak: Optional[Callable[[str], Awaitable[None]]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        hour: Callable[[], int] = lambda: time.localtime().tm_hour,
    ):
        self._registry = registry or default_device_registry
        self._bus = bus or default_event_bus
        self._store = store
        self._recognizer_factory = recognizer_factory or self._default_recognizer
        self._gateway_factory = gateway_factory
        self._speak_impl = speak
        self._clock = clock
        self._sleep = sleep
        self._hour = hour

        #: ``None`` until started: then it follows CAMERA_WATCH_ENABLED, and the
        #: camera_watch tool flips it at runtime without touching .env.
        self.enabled: Optional[bool] = None
        self._task: Optional[asyncio.Task] = None
        self._recognizer: Optional[FaceRecognizer] = None
        self._recognizer_missing = False

        self._last_greet: Dict[str, float] = {}
        self._last_look_at = -1e9
        self._last_object = ""
        self._last_object_at = -1e9
        self._failures = 0
        self._quiet_until = 0.0
        self.stats: Dict[str, Any] = {
            "ticks": 0, "looks": 0, "greetings": 0, "strangers": 0,
            "objects": 0, "errors": 0, "last_seen": [], "last_object": "",
        }

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._task is not None:
            return
        if self.enabled is None:
            self.enabled = bool(settings.CAMERA_WATCH_ENABLED)
        self._task = asyncio.create_task(self._run(), name="camera-watch")
        logger.info("Camera watch service started (%s).", "watching" if self.enabled else "paused")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None

    def set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)
        if value:
            # A fresh start should greet again, not sit out a stale cooldown.
            self._last_greet.clear()
            self._quiet_until = 0.0
            self._failures = 0

    def status(self) -> Dict[str, Any]:
        camera = self._registry.first_of_kind("camera")
        backend = self._recognizer.name if self._recognizer is not None else None
        return {
            "enabled": bool(self.enabled),
            "running": self._task is not None,
            "camera": camera.name if camera is not None else None,
            "face_backend": backend,
            "can_identify": bool(self._recognizer is not None and self._recognizer.spec.can_embed),
            "objects": bool(settings.CAMERA_WATCH_OBJECTS and settings.VISION_MODEL),
            "strangers": bool(settings.CAMERA_ANNOUNCE_STRANGERS),
            "paused_for_s": max(0, round(self._quiet_until - self._clock())),
            **self.stats,
        }

    # ------------------------------------------------------------------ loop
    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the watcher must outlive any surprise
                self.stats["errors"] += 1
                logger.debug("Camera watch tick failed: %s", exc, exc_info=True)
            await self._sleep(self._interval())

    def _interval(self) -> float:
        if not self.enabled or self._registry.first_of_kind("camera") is None:
            return NO_CAMERA_POLL_S
        return max(0.2, float(settings.CAMERA_WATCH_INTERVAL_S))

    # ------------------------------------------------------------------ tick
    async def tick(self) -> Optional[str]:
        """One pass. Returns a short word saying what happened — for the status
        tool and the tests — or ``None`` when there was nothing to do."""
        if not self.enabled:
            return None
        now = self._clock()
        if now < self._quiet_until:
            return "paused"
        device = self._registry.first_of_kind("camera")
        if device is None or device.linked or not device.base_url:
            return None
        self.stats["ticks"] += 1

        from iris.app.tools.devices.camera import camera_eye_for

        eye = camera_eye_for(device)
        try:
            presence = await eye.presence()
            if presence.enabled:
                if not presence.ready:
                    return "warming"
                if not presence.somebody_there:
                    return "quiet"
                settled = presence.recent and not presence.moved
                min_gap = LOOK_COOLDOWN_S
            else:
                # Older firmware, or ENABLE_MOTION off: look on a slow timer.
                settled = True
                min_gap = BLIND_LOOK_INTERVAL_S
            if now - self._last_look_at < min_gap:
                return "cooldown"
            self._last_look_at = now

            frame = await eye.capture(size=FACE_FRAME_SIZE, warmup=1)
            self.stats["looks"] += 1
            recognizer = self._recognizer_or_none()
            sighting: Optional[Sighting] = None
            if recognizer is not None:
                store = self.store if recognizer.spec.can_embed else None
                sighting = await asyncio.to_thread(look, frame, recognizer, store)
            self._failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - RobotEyeError, ToolError, decode errors
            return self._note_failure(exc)

        if sighting is not None and sighting.faces:
            return await self._on_faces(device, sighting, now)
        if settled and settings.CAMERA_WATCH_OBJECTS and settings.VISION_MODEL:
            return await self._on_maybe_object(frame, now)
        return "nothing"

    # ----------------------------------------------------------------- faces
    async def _on_faces(self, device: Device, sighting: Sighting, now: float) -> str:
        from iris.app.tools.devices.camera import gaze_for

        cooldown = float(settings.CAMERA_GREET_COOLDOWN_S)
        names = [face.name for face in sighting.known]
        self.stats["last_seen"] = names
        self._bus.publish(
            Topics.VISION_SIGHTING,
            {"event": "watch", "camera": device.name, "faces": sighting.face_count, "known": names},
        )

        spoke = False
        owner = self.store.owner()
        for face in sighting.known:
            if now - self._last_greet.get(face.name, -1e9) < cooldown:
                continue
            self._last_greet[face.name] = now
            is_owner = owner is not None and owner.name == face.name
            sentence = greeting_for(face.name, owner=is_owner, hour=self._hour())
            self.stats["greetings"] += 1
            self._bus.publish(Topics.VISION_SIGHTING, {"event": "greeted", "name": face.name})
            await self._announce(sentence, emotion="happy", gaze=gaze_for(sighting, face))
            spoke = True
        if spoke:
            return "greeted"

        strangers = [face for face in sighting.unknown if not face.too_small]
        if strangers and settings.CAMERA_ANNOUNCE_STRANGERS and not sighting.known:
            if now - self._last_greet.get(STRANGER_KEY, -1e9) >= cooldown:
                self._last_greet[STRANGER_KEY] = now
                self.stats["strangers"] += 1
                self._bus.publish(Topics.VISION_SIGHTING, {"event": "stranger", "faces": len(strangers)})
                await self._announce(
                    "Someone I don't recognise is here.",
                    emotion="suspicious", gaze=gaze_for(sighting, strangers[0]),
                )
                return "stranger"
        return "seen"

    # --------------------------------------------------------------- objects
    async def _on_maybe_object(self, frame: bytes, now: float) -> str:
        if now - self._last_object_at < float(settings.CAMERA_OBJECT_COOLDOWN_S):
            return "nothing"
        self._last_object_at = now
        gateway = self._gateway()
        try:
            response = await gateway.generate(
                "", messages=build_vision_messages(frame, OBJECT_WATCH_PROMPT),
                capability="VISION", model=settings.VISION_MODEL, max_tokens=120,
            )
        except Exception as exc:  # noqa: BLE001 - a slow free model is not an error worth speaking
            logger.debug("Vision model unavailable for the watch: %s", exc)
            return "nothing"
        text = (getattr(response, "content", "") or "").strip()
        if not text or text.strip(" .!").lower() == NOTHING or text.lower().startswith(NOTHING):
            return "nothing"
        shaped = shape_answer(text, OBJECT_WATCH_PROMPT, model=settings.VISION_MODEL or "",
                              frame_bytes=len(frame), max_spoken_sentences=1)
        sentence = shaped.speech.strip()
        if not sentence or sentence.lower() == self._last_object.lower():
            return "nothing"
        self._last_object = sentence
        self.stats["objects"] += 1
        self.stats["last_object"] = sentence
        self._bus.publish(Topics.VISION_SIGHTING, {"event": "object", "text": sentence})
        await self._announce(sentence, emotion="surprised", gaze=None)
        return "object"

    # --------------------------------------------------------------- helpers
    @property
    def store(self) -> FaceStore:
        if self._store is None:
            from iris.app.tools.devices.camera import faces_path

            self._store = FaceStore(faces_path())
        return self._store

    def _default_recognizer(self) -> Optional[FaceRecognizer]:
        try:
            return FaceRecognizer(select_backend(module_available, need_embeddings=False))
        except VisionUnavailable as exc:
            logger.info("Camera watch: %s — watching for objects only.", exc)
            return None

    def _recognizer_or_none(self) -> Optional[FaceRecognizer]:
        if self._recognizer is None and not self._recognizer_missing:
            self._recognizer = self._recognizer_factory()
            self._recognizer_missing = self._recognizer is None
        return self._recognizer

    def _gateway(self) -> Any:
        if self._gateway_factory is not None:
            return self._gateway_factory()
        from iris.app.llm.gateway import default_model_gateway

        return default_model_gateway

    def _note_failure(self, exc: Exception) -> str:
        self._failures += 1
        self.stats["errors"] += 1
        if self._failures >= FAILURES_BEFORE_BACKOFF:
            self._quiet_until = self._clock() + BACKOFF_S
            self._failures = 0
            logger.info("Camera unreachable (%s) — pausing the watch for %.0fs.", exc, BACKOFF_S)
        else:
            logger.debug("Camera watch look failed: %s", exc)
        return "error"

    async def _announce(self, sentence: str, *, emotion: str, gaze: Optional[Dict[str, int]]) -> None:
        """Say it, and let the OLED eyes react — neither may raise."""
        try:
            if self._speak_impl is not None:
                await self._speak_impl(sentence)
            else:
                from iris.app.voice.service import default_voice_service

                await default_voice_service.speak(sentence)
        except Exception as exc:  # noqa: BLE001 - still show it on the face and the UI
            logger.warning("Could not speak the sighting (%s): %s", sentence, exc)
            self._bus.publish(
                Topics.VOICE_SPEAKING, {"text": sentence, "engine": "silent", "language": "en"},
            )
        try:
            from iris.app.tools.devices.face import push_face

            face = self._registry.first_of_kind("face")
            if face is not None:
                await asyncio.wait_for(
                    push_face(
                        face, emotion=emotion, hold_ms=FACE_HOLD_MS,
                        look=(gaze["x"], gaze["y"]) if gaze else None,
                    ),
                    timeout=2.0,
                )
        except Exception as exc:  # noqa: BLE001 - no face board is normal
            logger.debug("Could not react on the face: %s", exc)


default_camera_watch_service = CameraWatchService()
