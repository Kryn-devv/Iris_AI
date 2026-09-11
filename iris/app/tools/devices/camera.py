"""The robot's eye as IRIS tools — seeing, recognising and remembering faces.

Goes to ``iris/app/tools/devices/camera.py``. Companion to ``devices/face.py``,
which drives the OLED eyes: that one is the face the robot *shows*, this one is
the faces it *sees*.

WHAT RUNS WHERE, AND WHY
The ESP32-CAM is a camera and nothing more. It cannot recognise a face —
Espressif's ESP-WHO / ESP-DL dropped the original ESP32, and the Arduino core
removed the old ``fd_forward.h`` face headers after 3.0.7 — so it sends frames
and the two hard questions are answered here, differently:

* **"who am I?"** is answered locally, by comparing the face against samples
  the robot has been shown. No cloud call: it is a closed comparison, it has
  to work when the internet is down, and most hosted vision models will
  refuse to identify a named individual anyway.
* **"what is this?"** goes to a vision model, because naming an arbitrary
  object is open-ended and that is exactly what those models are good at.

The vision call needs no change to the LLM gateway. ``CloudLLMProvider.generate``
builds its request as ``_build_messages(prompt, system_prompt, kwargs["messages"])``
and posts it to an OpenAI-compatible ``/chat/completions``, so passing a
ready-made ``messages`` list carries the standard ``image_url`` content block
straight through.

See ``docs/CAMERA.md`` for wiring and setup; the firmware lives in the
``cammodule`` repository (``esp32-cam/robot_eye/``).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from iris.app.core import paths
from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError
from iris.app.tools.devices.registry import Device, DeviceRegistry, default_device_registry
from iris.app.tools.devices.transport import (
    LAN_TIMEOUT,
    _client,
    _looks_unresolvable,
    device_request,
    lan_get,
)
from iris.app.vision.robot_eye import (
    CameraEye,
    FaceRecognizer,
    FaceStore,
    RobotEyeError,
    VisionUnavailable,
    describe_sighting,
    look,
    module_available,
    select_backend,
)
from iris.app.vision.robot_eye.camera import FACE_FRAME_SIZE
from iris.app.vision.robot_eye.describe import (
    PROMPTS,
    build_vision_messages,
    no_vision_model_message,
    question_for,
    shape_answer,
)
from iris.app.vision.robot_eye.recognize import (
    describe_presence_only,
    enroll as enroll_face,
)

logger = get_logger("tools.devices.camera")

FACES_FILENAME = "faces.json"

#: A frame for a vision model does not need to be big — the model resizes it
#: anyway, and a 200 KB base64 body is what makes a free-tier request fail.
LOOK_FRAME_SIZE = "svga"
#: Reading a label or a screen is the one job that genuinely wants the pixels.
TEXT_FRAME_SIZE = "xga"

#: Frames dropped before the shot so auto-exposure has settled. The camera is
#: usually idle between requests, and its first frame after that is often
#: brighter or darker than the room really is.
CAPTURE_WARMUP = 2

#: A frame is not a JSON reading: an XGA JPEG with two warm-up frames takes the
#: camera a second or two, and a hotspot adds its share. Longer than the
#: device default, still short enough that a dead camera is reported promptly.
FRAME_TIMEOUT = httpx.Timeout(10.0, connect=2.0)


def faces_path() -> Path:
    """Where enrolled faces live — beside devices.json in the data directory."""
    return paths.data_dir() / FACES_FILENAME


async def _lan_get_bytes(url: str, params: Optional[Dict[str, Any]] = None) -> bytes:
    """GET a binary body from a device.

    ``transport.lan_get`` exists but parses JSON, and a JPEG is not JSON. The
    error wording is kept deliberately identical to it, so a camera that is
    off says the same thing as a relay that is off. It lives here rather than
    in ``transport.py`` because the camera is the only thing that answers in
    binary; move it there the moment something else does.
    """
    host = url.split("/", 3)[2] if "//" in url else url
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        # The device transport's client: no proxy lookup, no SSL context for
        # http — the 43 ms that used to precede every device call.
        async with _client(url, FRAME_TIMEOUT) as client:
            response = await client.get(url, params=clean)
    except httpx.ConnectError as exc:
        if _looks_unresolvable(exc):
            raise ToolError(
                f"Could not look up '{host}' on this network — register the camera "
                "by its IP address instead of a .local name."
            ) from exc
        raise ToolError(
            f"Could not reach the camera at {host} — is it powered on and on the same WiFi?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ToolError(f"The camera at {host} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"Camera request failed: {exc}") from exc

    if response.status_code == 401:
        raise ToolError(
            "The camera refused the request: it has an access token set and IRIS "
            "does not have it. Re-register the device with the token in its notes."
        )
    if response.status_code >= 400:
        raise ToolError(
            f"The camera answered HTTP {response.status_code}: {response.text[:120]}"
        )
    return response.content


class _CameraToolBase(BaseTool):
    """Shared plumbing: find the camera, and talk to it."""

    category = ToolCategory.AUTOMATION
    network = True

    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
        store: Optional[FaceStore] = None,
    ):
        self.registry = registry or default_device_registry
        self._store = store

    # -- the device ------------------------------------------------------
    def _pick_camera(self, name: Optional[str] = None) -> Device:
        device = self.registry.get(name) if name else self.registry.first_of_kind("camera")
        if device is None:
            if name:
                raise ToolError(f"I don't have a camera called '{name}' registered.")
            raise ToolError(
                "No camera is registered yet. Flash the robot_eye firmware, read the "
                "address it prints, and say: add device eye at <address> as camera"
            )
        if device.kind != "camera":
            raise ToolError(
                f"'{device.name}' is registered as a {device.kind}, not a camera."
            )
        if device.linked:
            # A linked device reaches IRIS over a WebSocket, which carries JSON
            # commands. Relaying every JPEG down it to a VPS would be slow and
            # is not what anyone wants: keep the camera and the brain on one LAN.
            raise ToolError(
                f"'{device.name}' is connected over the cloud link, which cannot carry "
                "frames. A camera has to be on the same network as IRIS — re-register it "
                "with its LAN address."
            )
        if not device.base_url:
            raise ToolError(f"'{device.name}' has no address on record.")
        return device

    def _eye(self, device: Device) -> CameraEye:
        """A :class:`CameraEye` wired to this device's address.

        The token travels in the device's ``notes`` field, which is the only
        per-device free-text the registry already persists — so setting one
        needs no schema change and no second place to look.
        """
        token = self._token_from(device)

        async def fetch_bytes(path: str, params: Dict[str, Any]) -> bytes:
            return await _lan_get_bytes(f"{device.base_url}{path}", params)

        async def fetch_json(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
            return await lan_get(f"{device.base_url}{path}", params)

        return CameraEye(fetch_bytes, fetch_json, token=token, name=device.name)

    @staticmethod
    def _token_from(device: Device) -> Optional[str]:
        for part in str(device.notes or "").split():
            if part.startswith("token="):
                return part[len("token="):] or None
        return None

    # -- the faces -------------------------------------------------------
    @property
    def store(self) -> FaceStore:
        if self._store is None:
            self._store = FaceStore(faces_path())
        return self._store

    def _recognizer(self, need_embeddings: bool = True) -> FaceRecognizer:
        """The best installed face backend, or a clear reason there is none."""
        try:
            spec = select_backend(module_available, need_embeddings=need_embeddings)
        except VisionUnavailable as exc:
            raise ToolError(str(exc), speech=str(exc)) from exc
        return FaceRecognizer(spec)

    async def _turn_eyes(self, gaze: Optional[Dict[str, int]]) -> bool:
        """Point the S3 board's OLED eyes at what the camera found.

        Best effort: the face board may not be registered or may be off, and
        neither must turn "that's you" into an error. Returns whether it was
        asked.
        """
        if not gaze:
            return False
        face = self.registry.first_of_kind("face")
        if face is None:
            return False
        try:
            await device_request(face, "/look", {"x": int(gaze["x"]), "y": int(gaze["y"])})
            return True
        except ToolError as exc:
            logger.debug("Could not turn the eyes toward the sighting: %s", exc)
            return False

    @staticmethod
    def _publish(event: str, payload: Dict[str, Any]) -> None:
        """Announce a sighting on the bus, if the topic exists.

        ``getattr`` rather than a hard reference so this file works before
        ``Topics`` gains a vision entry — one less edit to get running.
        """
        topic = getattr(Topics, "VISION_SIGHTING", None) or Topics.SYSTEM_NOTICE
        try:
            default_event_bus.publish(topic, {"event": event, **payload})
        except Exception:  # noqa: BLE001 - the bus must never break a tool
            logger.debug("Could not publish %s on %s", event, topic, exc_info=True)


class CameraLookTool(_CameraToolBase):
    name = "camera_look"
    description = (
        "Look through the robot's camera and say what is there. Identifies objects held "
        "up or placed in front of it, reads labels and screens, and judges whether fruit "
        "looks ripe. Answers 'what do you see', 'what is this', 'what am I holding', "
        "'is this ripe', 'read this'. Needs a vision-capable model configured."
    )
    permission_level = PermissionLevel.READ
    aliases = [
        "what do you see", "what is this", "what am I holding", "look at this",
        "identify this", "is this ripe", "read this", "what's in front of you",
    ]
    input_schema = ToolParameterSchema(
        properties={
            "kind": {
                "type": "string",
                "enum": sorted(PROMPTS),
                "description": (
                    "What sort of question this is: 'object' for something held up, "
                    "'scene' for the whole view, 'ripeness' for fruit, 'text' to read "
                    "a label, 'count' to enumerate, 'person' for what someone is doing."
                ),
            },
            "question": {
                "type": "string",
                "description": "The user's own question, if they asked something specific.",
            },
            "device": {"type": "string", "description": "Camera name (defaults to the first one)."},
            "flash": {
                "type": "boolean",
                "description": "Pulse the white LED for the shot. Use when the room is dark.",
            },
        },
    )
    examples = [
        ToolExample(utterance="what do you see", arguments={"kind": "scene"}),
        ToolExample(utterance="what is this I'm holding", arguments={"kind": "object"}),
        ToolExample(utterance="is this apple ripe", arguments={"kind": "ripeness"}),
        ToolExample(utterance="read this label for me", arguments={"kind": "text"}),
        ToolExample(utterance="how many things are in front of you", arguments={"kind": "count"}),
    ]

    def __init__(self, registry=None, store=None, gateway=None):
        super().__init__(registry, store)
        self._gateway = gateway

    def _resolve_gateway(self):
        if self._gateway is not None:
            return self._gateway
        from iris.app.llm.gateway import default_model_gateway

        return default_model_gateway

    async def _run(
        self,
        kind: str = "scene",
        question: Optional[str] = None,
        device: Optional[str] = None,
        flash: bool = False,
    ) -> Dict[str, Any]:
        from iris.app.core.config import settings

        # Checked before the camera is disturbed: with no vision model there is
        # nothing useful to do with a frame, and saying so beats a shrug.
        if not settings.VISION_MODEL:
            message = no_vision_model_message()
            raise ToolError(message, speech=message)

        target = self._pick_camera(device)
        eye = self._eye(target)
        asked = question_for(kind, question)
        size = TEXT_FRAME_SIZE if (kind or "").lower() == "text" else LOOK_FRAME_SIZE

        try:
            frame = await eye.capture(
                size=size, warmup=CAPTURE_WARMUP, flash=bool(flash),
                flash_ms=250 if flash else None,
            )
        except RobotEyeError as exc:
            raise ToolError(str(exc), speech=str(exc)) from exc

        messages = build_vision_messages(frame, asked)
        gateway = self._resolve_gateway()

        # prompt="" because the question is already inside the multimodal
        # message; _build_messages only appends a prompt when it is truthy, so
        # this avoids asking the same thing twice.
        response = await gateway.generate(
            "", messages=messages, capability="VISION",
            model=settings.VISION_MODEL, max_tokens=400,
        )

        shaped = shape_answer(
            getattr(response, "content", "") or "",
            asked,
            model=getattr(response, "model_name", "") or settings.VISION_MODEL,
            frame_bytes=len(frame),
        )
        logger.info("camera_look (%s) via %s, %d byte frame", kind, shaped.model, len(frame))
        return {
            "speech": shaped.speech,
            "display": shaped.display,
            "question": shaped.question,
            "kind": kind,
            "model": shaped.model,
            "frame_bytes": shaped.frame_bytes,
            "camera": target.name,
        }


class CameraWhoTool(_CameraToolBase):
    name = "camera_who"
    description = (
        "Look through the robot's camera and say who is there, by comparing faces "
        "against the people it has been introduced to. Answers 'who am I', 'do you "
        "recognise me', 'who is that', 'can you see me'. Runs entirely on this machine."
    )
    permission_level = PermissionLevel.READ
    aliases = [
        "who am i", "who is that", "do you recognise me", "do you know me",
        "can you see me", "who do you see", "look at me",
    ]
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Camera name (defaults to the first one)."},
            "flash": {"type": "boolean", "description": "Pulse the white LED. Use in the dark."},
            "turn_eyes": {"type": "boolean",
                          "description": "Turn the robot's OLED eyes toward the person found (default true)."},
        },
    )
    examples = [
        ToolExample(utterance="who am I", arguments={}),
        ToolExample(utterance="do you recognise me", arguments={}),
        ToolExample(utterance="who is in front of you", arguments={}),
    ]

    async def _run(
        self, device: Optional[str] = None, flash: bool = False, turn_eyes: bool = True,
    ) -> Dict[str, Any]:
        target = self._pick_camera(device)
        eye = self._eye(target)

        # Detection-only is still worth having: "there is someone there" is a
        # real answer, and better than refusing because nothing can name them.
        recognizer = self._recognizer(need_embeddings=False)
        can_name = recognizer.spec.can_embed

        try:
            # A bigger frame here than for object questions: pixels across the
            # face is the single biggest lever on whether this works at all.
            frame = await eye.capture(
                size=FACE_FRAME_SIZE, warmup=CAPTURE_WARMUP,
                flash=bool(flash), flash_ms=250 if flash else None,
            )
            sighting = await self.to_thread(
                look, frame, recognizer, self.store if can_name else None
            )
        except RobotEyeError as exc:
            raise ToolError(str(exc), speech=str(exc)) from exc

        speech = (
            describe_sighting(sighting, self.store) if can_name
            else describe_presence_only(sighting)
        )

        faces = [
            {
                "name": face.name,
                "confidence": round(face.match.confidence, 3) if face.match else None,
                "distance": round(face.match.distance, 4) if face.match else None,
                "box": face.box.as_dict(),
                "fraction": round(face.fraction, 4),
                "too_far": face.too_small,
            }
            for face in sighting.faces
        ]

        self._publish("seen", {
            "camera": target.name,
            "faces": len(faces),
            "known": [face.name for face in sighting.known],
        })

        # Where the person is, in the -100..100 the S3 node's /look takes —
        # and the eyes are turned there, so the robot looks at who it names.
        # (Named ``gaze`` on purpose: ``look`` is the recogniser call above.)
        gaze = None
        if sighting.primary is not None:
            cx, cy = sighting.primary.box.center
            gaze = {
                "x": max(-100, min(100, round((cx / max(1, sighting.frame_width)) * 200 - 100))),
                "y": max(-100, min(100, round((cy / max(1, sighting.frame_height)) * 200 - 100))),
            }
        eyes_turned = await self._turn_eyes(gaze) if turn_eyes else False

        return {
            "speech": speech,
            "display": speech,
            "camera": target.name,
            "backend": recognizer.name,
            "can_identify": can_name,
            "face_count": sighting.face_count,
            "faces": faces,
            "frame": {"width": sighting.frame_width, "height": sighting.frame_height},
            "look": gaze,
            "eyes_turned": eyes_turned,
        }


class CameraRememberFaceTool(_CameraToolBase):
    name = "camera_remember_face"
    description = (
        "Learn the face of whoever is in front of the camera and remember it under a "
        "name. Answers 'remember my face as X', 'this is X', 'learn my face'. Ask "
        "again a few times in different light to make recognition steadier."
    )
    permission_level = PermissionLevel.LOW_RISK_ACTION
    mutating = True
    aliases = [
        "remember my face", "learn my face", "this is me", "remember this face",
        "introduce myself",
    ]
    input_schema = ToolParameterSchema(
        properties={
            "name": {"type": "string", "description": "Who this face belongs to."},
            "owner": {
                "type": "boolean",
                "description": (
                    "True when this is the person the robot belongs to, so 'who am I' "
                    "can be answered as 'you'. Only one person can be the owner."
                ),
            },
            "device": {"type": "string", "description": "Camera name (defaults to the first one)."},
            "flash": {"type": "boolean", "description": "Pulse the white LED. Use in the dark."},
        },
        required=["name"],
    )
    examples = [
        ToolExample(utterance="remember my face as Prakash",
                    arguments={"name": "Prakash", "owner": True}),
        ToolExample(utterance="this is Aditi", arguments={"name": "Aditi"}),
    ]

    async def _run(
        self,
        name: str,
        owner: Optional[bool] = None,
        device: Optional[str] = None,
        flash: bool = False,
    ) -> Dict[str, Any]:
        if not str(name or "").strip():
            raise ToolError("I need a name to file that face under.",
                            speech="Tell me whose face this is.")

        target = self._pick_camera(device)
        eye = self._eye(target)
        recognizer = self._recognizer(need_embeddings=True)

        try:
            frame = await eye.capture(
                size=FACE_FRAME_SIZE, warmup=CAPTURE_WARMUP,
                flash=bool(flash), flash_ms=250 if flash else None,
            )
            # The first person introduced is the owner unless told otherwise:
            # it is almost always the person setting the robot up, and it makes
            # "who am I" work without a second command.
            is_owner = owner if owner is not None else (len(self.store) == 0)
            person, speech = await self.to_thread(
                enroll_face, frame, name, recognizer, self.store,
                now=time.time(), owner=is_owner,
            )
        except RobotEyeError as exc:
            raise ToolError(str(exc), speech=str(exc)) from exc

        self._publish("enrolled", {"name": person.name, "samples": person.sample_count})
        logger.info("Enrolled '%s' (%d samples, backend %s)",
                    person.name, person.sample_count, person.backend)

        return {
            "speech": speech,
            "display": speech,
            "name": person.name,
            "samples": person.sample_count,
            "owner": person.owner,
            "backend": person.backend,
            "camera": target.name,
        }


class CameraForgetFaceTool(_CameraToolBase):
    name = "camera_forget_face"
    description = (
        "Forget a face the robot has learned. Answers 'forget my face', "
        "'forget X's face', 'delete the face data'."
    )
    permission_level = PermissionLevel.LOW_RISK_ACTION
    mutating = True
    aliases = ["forget my face", "forget this face", "delete my face"]
    input_schema = ToolParameterSchema(
        properties={
            "name": {
                "type": "string",
                "description": "Whose face to forget. Omit together with everyone=true.",
            },
            "everyone": {"type": "boolean", "description": "Forget every enrolled face."},
        },
    )
    examples = [
        ToolExample(utterance="forget Aditi's face", arguments={"name": "Aditi"}),
        ToolExample(utterance="forget every face you know", arguments={"everyone": True}),
    ]

    async def _run(self, name: Optional[str] = None, everyone: bool = False) -> Dict[str, Any]:
        if everyone:
            count = len(self.store)
            self.store.clear()
            speech = (
                f"Forgotten — all {count} faces are gone." if count
                else "There were no faces to forget."
            )
            return {"speech": speech, "display": speech, "forgotten": count}

        if not str(name or "").strip():
            raise ToolError(
                "Tell me whose face to forget, or say to forget all of them.",
                speech="Whose face should I forget?",
            )

        if self.store.forget(name):
            speech = f"Forgotten. I won't recognise {name.strip()} any more."
            return {"speech": speech, "display": speech, "forgotten": 1, "name": name.strip()}

        speech = f"I don't have a face stored for {name.strip()}."
        return {"speech": speech, "display": speech, "forgotten": 0}


class CameraKnownFacesTool(_CameraToolBase):
    name = "camera_known_faces"
    description = (
        "List the people the robot has been introduced to and can recognise. "
        "Answers 'who do you know', 'whose faces do you remember'."
    )
    permission_level = PermissionLevel.READ
    network = False
    aliases = ["who do you know", "whose faces do you know", "list faces"]
    input_schema = ToolParameterSchema()
    examples = [ToolExample(utterance="who do you recognise", arguments={})]

    async def _run(self) -> Dict[str, Any]:
        summary = self.store.describe()
        people = summary["people"]

        if not people:
            speech = (
                "I haven't been introduced to anyone yet. Stand in front of the camera "
                "and say 'remember my face as' and your name."
            )
        else:
            parts = []
            for person in people:
                label = f"{person['name']} (you)" if person["owner"] else person["name"]
                parts.append(label)
            listed = ", ".join(parts)
            speech = (
                f"I know one face: {listed}." if len(parts) == 1
                else f"I know {len(parts)} faces: {listed}."
            )

        return {"speech": speech, "display": speech, **summary}


class CameraPresenceTool(_CameraToolBase):
    name = "camera_presence"
    description = (
        "Ask the camera whether anybody is in front of it, and where. Cheap — the "
        "camera watches for movement itself, so this needs no frame transfer and no "
        "face detection. Answers 'is anyone there', 'has anything moved'. Use this "
        "before camera_who when the answer is probably 'nobody'."
    )
    permission_level = PermissionLevel.READ
    aliases = ["is anyone there", "anybody there", "has anything moved", "any movement"]
    input_schema = ToolParameterSchema(
        properties={
            "device": {"type": "string", "description": "Camera name (defaults to the first one)."},
            "reset": {
                "type": "boolean",
                "description": "Forget the reference frame and start again from this one.",
            },
        },
    )
    examples = [
        ToolExample(utterance="is anyone in front of you", arguments={}),
        ToolExample(utterance="has anything moved", arguments={}),
    ]

    async def _run(self, device: Optional[str] = None, reset: bool = False) -> Dict[str, Any]:
        target = self._pick_camera(device)
        eye = self._eye(target)
        presence = await eye.presence(reset=bool(reset))

        if not presence.enabled:
            speech = (
                "This camera doesn't watch for movement — "
                f"{presence.reason or 'motion detection is off in its firmware.'}"
            )
        elif not presence.ready:
            speech = "The camera is still taking its first look; ask me again in a moment."
        elif presence.moved:
            where = self._where(presence.look)
            speech = f"Yes, something's moving{where}."
        elif presence.recent:
            seconds = max(1, presence.since_motion_ms // 1000)
            speech = f"Not right now, but something moved about {seconds} seconds ago."
        else:
            speech = "No, nothing's moving in front of me."

        gaze = {"x": presence.look[0], "y": presence.look[1]} if presence.look else None
        eyes_turned = await self._turn_eyes(gaze) if presence.moved else False

        return {
            "speech": speech,
            "display": speech,
            "camera": target.name,
            "enabled": presence.enabled,
            "ready": presence.ready,
            "moved": presence.moved,
            "recent": presence.recent,
            "somebody_there": presence.somebody_there,
            "percent": presence.percent,
            "since_motion_ms": presence.since_motion_ms,
            "box": presence.box,
            # Already in the -100..100 the S3 node's /look?x=&y= expects; the
            # eyes were pointed there above when something was moving.
            "look": gaze,
            "eyes_turned": eyes_turned,
        }

    @staticmethod
    def _where(look: Optional[tuple]) -> str:
        if not look:
            return ""
        x, y = look
        side = "to my left" if x < -25 else "to my right" if x > 25 else "straight ahead"
        if y < -35:
            return f" {side}, up high"
        if y > 35:
            return f" {side}, down low"
        return f" {side}"


def get_tools() -> list[BaseTool]:
    return [
        CameraLookTool(),
        CameraWhoTool(),
        CameraRememberFaceTool(),
        CameraForgetFaceTool(),
        CameraKnownFacesTool(),
        CameraPresenceTool(),
    ]
