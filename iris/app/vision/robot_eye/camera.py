"""Talking to the ESP32-CAM: frames in, presence in, nothing clever.

The transport is injected rather than imported. Two reasons: IRIS already owns
an ``httpx`` client with its own timeouts, LAN-address checks and error
messages, and this module has no business duplicating that; and it makes the
URL building and response parsing — where the bugs actually are — testable
without a network or a camera.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from .errors import NoFrame

#: Resolutions the firmware's /capture?size= accepts. Kept in step with
#: FRAME_SIZES in esp32-cam/robot_eye/robot_eye.ino.
FRAME_SIZES = ("qqvga", "qvga", "cif", "hvga", "vga", "svga", "xga", "hd", "sxga", "uxga")

#: What to ask for when the job is recognising a face rather than watching a
#: room. More pixels across a face is the single biggest lever on whether
#: recognition works at conversational distance.
FACE_FRAME_SIZE = "xga"

#: Smallest thing that could be a JPEG: SOI, one byte, EOI.
_MIN_JPEG_BYTES = 4
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


@dataclass(frozen=True)
class Presence:
    """The camera's own answer to "is anybody there?"."""

    enabled: bool = False
    ready: bool = False
    moved: bool = False
    recent: bool = False
    percent: int = 0
    changed_cells: int = 0
    since_motion_ms: int = 0
    samples: int = 0
    events: int = 0
    #: Where the change was, as (x, y) in the -100..100 range the S3 node's
    #: /look endpoint takes. ``None`` when nothing has moved.
    look: Optional[tuple] = None
    #: Bounding box of the change in per-mille of the frame, or ``None``.
    box: Optional[Dict[str, int]] = None
    reason: str = ""

    @property
    def somebody_there(self) -> bool:
        """True when something moved recently enough to be worth a look."""
        return self.enabled and (self.moved or self.recent)


def _as_int(value: Any, default: int = 0) -> int:
    """Tolerant int: custom firmware and old versions send surprises."""
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def parse_motion(payload: Mapping[str, Any]) -> Presence:
    """Turn a /motion response into a :class:`Presence`.

    Every field is optional on the wire. A camera running older firmware, or
    one built with ENABLE_MOTION off, answers with almost nothing — and must
    still parse into "no, and here is why" rather than raising.
    """
    if not isinstance(payload, Mapping):
        return Presence(enabled=False, reason="The camera's motion reply was not an object.")

    if not _as_bool(payload.get("enabled")):
        return Presence(
            enabled=False,
            reason=str(payload.get("reason") or "This camera does not report motion."),
        )

    look_raw = payload.get("look")
    look: Optional[tuple] = None
    if isinstance(look_raw, Mapping):
        look = (_as_int(look_raw.get("x")), _as_int(look_raw.get("y")))

    box_raw = payload.get("box")
    box: Optional[Dict[str, int]] = None
    if isinstance(box_raw, Mapping):
        box = {
            "x": _as_int(box_raw.get("x")),
            "y": _as_int(box_raw.get("y")),
            "w": _as_int(box_raw.get("w")),
            "h": _as_int(box_raw.get("h")),
        }

    return Presence(
        enabled=True,
        ready=_as_bool(payload.get("ready")),
        moved=_as_bool(payload.get("moved")),
        recent=_as_bool(payload.get("recent")),
        percent=_as_int(payload.get("percent")),
        changed_cells=_as_int(payload.get("changed_cells")),
        since_motion_ms=_as_int(payload.get("since_motion_ms")),
        samples=_as_int(payload.get("samples")),
        events=_as_int(payload.get("events")),
        look=look,
        box=box,
    )


def capture_params(
    size: Optional[str] = None,
    warmup: Optional[int] = None,
    flash: bool = False,
    flash_ms: Optional[int] = None,
    token: Optional[str] = None,
) -> Dict[str, str]:
    """Query parameters for /capture.

    Values are validated here rather than passed through, because the firmware
    answers a bad size with a 400 and the round trip is wasted.
    """
    params: Dict[str, str] = {}

    if size is not None:
        key = str(size).strip().lower()
        if key not in FRAME_SIZES:
            raise ValueError(
                f"'{size}' is not a resolution this camera offers "
                f"(pick one of: {', '.join(FRAME_SIZES)})."
            )
        params["size"] = key

    if warmup is not None:
        count = _as_int(warmup, -1)
        if count < 0 or count > 8:
            raise ValueError("warmup must be between 0 and 8 frames.")
        if count:
            params["warmup"] = str(count)

    if flash:
        params["flash"] = "1"
        if flash_ms is not None:
            ms = _as_int(flash_ms, -1)
            if ms < 10 or ms > 5000:
                raise ValueError("flash_ms must be between 10 and 5000.")
            params["flash_ms"] = str(ms)
    elif flash_ms is not None:
        raise ValueError("flash_ms only means something together with flash=True.")

    if token:
        params["token"] = str(token)
    return params


def looks_like_jpeg(data: bytes) -> bool:
    """Cheap sanity check on a captured frame.

    Worth doing: when the camera is unhappy it answers with an HTML error page
    or a JSON body, and handing that to a face detector produces a confusing
    failure a long way from the cause.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < _MIN_JPEG_BYTES:
        return False
    return bytes(data[:2]) == _JPEG_SOI


def check_frame(data: bytes) -> bytes:
    """Return the frame, or explain what arrived instead."""
    if not isinstance(data, (bytes, bytearray)):
        raise NoFrame("The camera did not send image data.")
    if len(data) < _MIN_JPEG_BYTES:
        raise NoFrame(f"The camera sent {len(data)} bytes, which is not a picture.")
    if not looks_like_jpeg(data):
        head = bytes(data[:48])
        if head.lstrip()[:1] in (b"{", b"<"):
            text = head.decode("utf-8", "replace").strip()
            raise NoFrame(f"The camera answered with a message, not a frame: {text!r}")
        raise NoFrame("The camera's reply was not a JPEG.")
    if bytes(data[-2:]) != _JPEG_EOI:
        # A frame cut short mid-transfer. Detectors will often still decode
        # part of it, which is worse than saying so.
        raise NoFrame(
            "The frame arrived incomplete (no end-of-image marker) — "
            "usually a WiFi drop or a camera short of power mid-capture."
        )
    return bytes(data)


@dataclass
class CameraEye:
    """One camera, reached through an injected transport.

    ``fetch_bytes(path, params) -> bytes`` and ``fetch_json(path, params) -> dict``
    are supplied by the caller. In IRIS they wrap the device transport so the
    LAN-only address rules and timeouts apply; in tests they are fakes.
    """

    fetch_bytes: Any
    fetch_json: Any
    token: Optional[str] = None
    name: str = "camera"
    _last_frame_bytes: int = field(default=0, init=False, repr=False)

    async def capture(
        self,
        size: Optional[str] = None,
        warmup: Optional[int] = None,
        flash: bool = False,
        flash_ms: Optional[int] = None,
    ) -> bytes:
        """One fresh JPEG, checked before it is handed on."""
        params = capture_params(
            size=size, warmup=warmup, flash=flash, flash_ms=flash_ms, token=self.token
        )
        raw = await self.fetch_bytes("/capture", params)
        frame = check_frame(raw)
        self._last_frame_bytes = len(frame)
        return frame

    async def presence(self, reset: bool = False) -> Presence:
        """Ask the cheap question: is anybody there, and where?"""
        params: Dict[str, str] = {}
        if reset:
            params["reset"] = "1"
        if self.token:
            params["token"] = self.token
        payload = await self.fetch_json("/motion", params)
        return parse_motion(payload if isinstance(payload, Mapping) else {})

    async def status(self) -> Dict[str, Any]:
        params = {"token": self.token} if self.token else {}
        payload = await self.fetch_json("/status", params)
        return dict(payload) if isinstance(payload, Mapping) else {}

    @property
    def last_frame_bytes(self) -> int:
        return self._last_frame_bytes
