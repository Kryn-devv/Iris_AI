"""The robot's face — two OLED eyes on an ESP32-S3 node.

IRIS has no screen on the robot itself, so the eyes are how it shows what it
is doing. Two things drive them:

* :class:`FaceEmotionTool` — an explicit request ("look happy", "wink").
* :func:`infer_emotion` — what the face does *by itself* every time IRIS
  speaks, so the expression follows the sentence without anyone asking.

The inference is deliberately a pure keyword-and-punctuation function rather
than an LLM call. It runs on the speech path, where an extra network round
trip would delay the voice, and it must work with no API key at all. It reads
English, Hindi and Hinglish because that is how this assistant is spoken to.

See ``docs/ESP32.md`` and ``firmware/esp32-s3-iris-sensors``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError
from iris.app.tools.devices.esp32 import _device_get
from iris.app.tools.devices.registry import DeviceRegistry, default_device_registry

logger = get_logger("tools.devices.face")

#: Must match ``EMOTION_NAMES`` in ``firmware/esp32-s3-iris-sensors/eyes.h``.
EMOTIONS = (
    "neutral", "happy", "excited", "love", "sad", "angry",
    "surprised", "sleepy", "thinking", "confused", "listening",
    "wink", "suspicious", "dizzy",
)

#: Spoken synonyms the firmware also accepts, mirrored here so the tool can
#: validate before making a network call.
SYNONYMS = {
    "smile": "happy", "glad": "happy",
    "mad": "angry", "cross": "angry",
    "shock": "surprised", "shocked": "surprised", "wow": "surprised",
    "think": "thinking", "thoughtful": "thinking",
    "listen": "listening",
    "sleep": "sleepy", "tired": "sleepy",
    "idle": "neutral", "normal": "neutral", "calm": "neutral",
    "khush": "happy", "udaas": "sad", "gussa": "angry",
    "pyaar": "love", "hairaan": "surprised", "neend": "sleepy",
}

#: A comfortable spoken pace, in words per minute, across the TTS engines IRIS
#: uses. Only needs to be close: the firmware caps the talking animation and
#: expires it on its own, so an overestimate cannot strand the eyes.
WORDS_PER_MINUTE = 165
MIN_SPEAK_MS = 700
MAX_SPEAK_MS = 30_000        # the firmware's own ceiling; kept in step

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def normalize_emotion(name: str) -> Optional[str]:
    """Canonical emotion name, or ``None`` when it is not one we know."""
    key = " ".join(str(name or "").strip().lower().split())
    if key in EMOTIONS:
        return key
    return SYNONYMS.get(key)


def estimate_speech_ms(text: str) -> int:
    """How long a sentence takes to say out loud, in milliseconds.

    The face is told this up front so the talking animation stops on its own.
    A dropped "finished speaking" packet then costs nothing — the same reason
    the robot's motor node auto-stops instead of trusting a stop command to
    arrive.
    """
    words = len(_WORD_RE.findall(text or ""))
    if words == 0:
        return 0
    millis = int(words * 60_000 / WORDS_PER_MINUTE)
    # Punctuation is where a voice pauses, and pauses are real time on screen.
    millis += 220 * (text.count(",") + text.count(";") + text.count(":"))
    millis += 380 * (text.count(".") + text.count("!") + text.count("?"))
    return max(MIN_SPEAK_MS, min(MAX_SPEAK_MS, millis))


# ---------------------------------------------------------------------------
# Emotion inference
# ---------------------------------------------------------------------------

# Ordered most-specific first: the first group with a hit wins, so "sorry, I
# could not find that" reads as apologetic rather than as a plain answer.
# Words are matched on whole-word boundaries, so "sadly" does not fire on
# "sad" by accident and "won" does not fire inside "wonder".
_EMOTION_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sad", (
        "sorry", "afraid", "unfortunately", "could not", "couldn't", "cannot",
        "can't", "failed", "no luck", "unable", "went wrong", "error",
        "nahi mila", "nahi ho paya", "maaf", "galat",
    )),
    ("angry", (
        "denied", "refused", "not allowed", "forbidden", "blocked",
        "dangerous", "mana hai", "not permitted",
    )),
    ("excited", (
        "done", "finished", "complete", "completed", "success", "successfully",
        "ready", "here you go", "all set", "got it", "launched", "opened",
        "ho gaya", "taiyar", "mil gaya", "shuru",
    )),
    ("happy", (
        "hello", "hi", "hey", "good morning", "good evening", "good afternoon",
        "welcome", "thanks", "thank you", "of course",
        "namaste", "namaskar", "shukriya", "dhanyavaad", "bahut accha",
    )),
    ("love", ("love you", "i love", "you're the best", "youre the best", "favourite", "favorite")),
    ("surprised", ("wow", "whoa", "incredible", "amazing", "unbelievable", "arre", "waah")),
    ("thinking", (
        "let me", "checking", "looking", "searching", "one moment", "hold on",
        "working on", "give me a second", "ruko", "dekh raha", "dhoond raha",
    )),
    ("sleepy", ("good night", "goodnight", "shutting down", "going to sleep", "shubh ratri")),
    ("confused", ("not sure", "unclear", "did you mean", "i don't understand", "samajh nahi")),
)

_QUESTION_RE = re.compile(r"\?\s*$")
_EXCLAIM_RE = re.compile(r"!")


def infer_emotion(text: str, default: str = "neutral") -> str:
    """Pick the expression that fits a sentence IRIS is about to say.

    Punctuation is only a tie-breaker. A cue phrase always wins, because
    "Sorry, that failed!" is apologetic despite the exclamation mark — reading
    the mark first would put a delighted face on bad news.
    """
    # Coerced once, up front: this runs on the speech path and the payload can
    # come from anywhere on the bus, so a non-string must not raise here.
    raw = str(text) if text is not None else ""
    lowered = " " + " ".join(raw.lower().split()) + " "
    if not lowered.strip():
        return default

    for emotion, cues in _EMOTION_CUES:
        for cue in cues:
            if " " in cue:
                if cue in lowered:
                    return emotion
            elif re.search(rf"\b{re.escape(cue)}\b", lowered):
                return emotion

    if _QUESTION_RE.search(raw.strip()):
        return "thinking"
    if _EXCLAIM_RE.search(raw):
        return "excited"
    return default


# ---------------------------------------------------------------------------
# Pushing to the node
# ---------------------------------------------------------------------------


async def push_face(
    base_url: str,
    *,
    emotion: Optional[str] = None,
    speak_ms: Optional[int] = None,
    hold_ms: Optional[int] = None,
    look: Optional[tuple[int, int]] = None,
    blink: Optional[int] = None,
) -> Dict[str, Any]:
    """Send one combined /face request — mood, gaze, speech and blink at once.

    One round trip rather than four: the eyes should change with the voice, not
    a beat behind it.
    """
    params: Dict[str, Any] = {}
    if emotion:
        params["emotion"] = emotion
    if speak_ms is not None:
        params["speak_ms"] = max(0, int(speak_ms))
    if hold_ms is not None:
        params["hold_ms"] = max(0, int(hold_ms))
    if look is not None:
        params["look_x"] = max(-100, min(100, int(look[0])))
        params["look_y"] = max(-100, min(100, int(look[1])))
    if blink:
        params["blink"] = max(1, min(5, int(blink)))
    return await _device_get(f"{base_url}/face", params=params)


class FaceEmotionTool(BaseTool):
    """Set the robot's expression on demand.

    IRIS already matches its face to whatever it says, so this is for when the
    user asks directly — "look happy", "wink at me", "aankh maaro".
    """

    name = "face_emotion"
    description = (
        "Show an expression on the robot's OLED eyes (happy, sad, angry, excited, love, "
        "surprised, sleepy, thinking, confused, listening, wink, suspicious, dizzy, neutral). "
        "Can also aim the gaze and blink."
    )
    category = ToolCategory.AUTOMATION
    permission_level = PermissionLevel.LOW_RISK_ACTION
    aliases = ["set expression", "robot face", "eyes", "look happy", "wink"]
    network = True
    input_schema = ToolParameterSchema(
        properties={
            "emotion": {
                "type": "string",
                "enum": list(EMOTIONS),
                "description": "Expression to show",
            },
            "device": {"type": "string", "description": "Face node name (defaults to the first face device)"},
            "seconds": {
                "type": "number",
                "description": "Hold for this many seconds then return to neutral (0 = keep it)",
            },
            "look": {
                "type": "string",
                "enum": ["centre", "center", "left", "right", "up", "down", "away"],
                "description": "Where to point the eyes",
            },
            "blink": {"type": "boolean", "description": "Blink once as well"},
        },
        required=["emotion"],
    )
    examples = [
        ToolExample(utterance="look happy", arguments={"emotion": "happy"}),
        ToolExample(utterance="wink at me", arguments={"emotion": "wink", "seconds": 2}),
        ToolExample(utterance="show angry eyes", arguments={"emotion": "angry"}),
        ToolExample(utterance="look to the left", arguments={"emotion": "neutral", "look": "left"}),
    ]

    _LOOKS = {
        "centre": (0, 0), "center": (0, 0),
        "left": (-90, 0), "right": (90, 0),
        "up": (0, -85), "down": (0, 80),
        "away": (-70, -55),
    }

    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or default_device_registry

    async def _run(
        self,
        emotion: str,
        device: Optional[str] = None,
        seconds: Optional[float] = None,
        look: Optional[str] = None,
        blink: bool = False,
    ) -> Dict[str, Any]:
        canonical = normalize_emotion(emotion)
        if canonical is None:
            raise ToolError(
                f"'{emotion}' is not an expression I can show. Try: {', '.join(EMOTIONS)}.",
                speech="I don't know that expression.",
            )

        target = self.registry.get(device) if device else self.registry.first_of_kind("face")
        if target is None:
            raise ToolError(
                "No face node is registered. Flash firmware/esp32-s3-iris-sensors and say: "
                "add device face at 192.168.1.70 as face",
                speech="I don't have a face connected yet.",
            )

        gaze = self._LOOKS.get((look or "").strip().lower()) if look else None
        hold_ms = int(max(0.0, float(seconds)) * 1000) if seconds else 0

        data = await push_face(
            target.base_url,
            emotion=canonical,
            hold_ms=hold_ms,
            look=gaze,
            blink=1 if blink else None,
        )
        return {
            "device": target.name,
            "emotion": canonical,
            "response": data,
            "speech": f"Showing {canonical}.",
            "display": f"{target.name}: {canonical}",
        }


def get_tools() -> list[BaseTool]:
    return [FaceEmotionTool()]
