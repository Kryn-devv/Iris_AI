"""Asking a vision model what it is looking at.

WHY THIS IS A DIFFERENT MECHANISM FROM FACES
"What am I holding?" and "who am I?" look like one problem and are not. Naming
an object is open-ended and a general vision model is very good at it. Deciding
that a particular face is *yours* is a closed comparison against samples the
robot has been shown, needs no cloud round trip, and — importantly — is
something most hosted vision models will refuse to do for a named individual.
So objects go out to the model and faces stay local. See ``faces.py``.

HOW THE IMAGE REACHES THE MODEL
IRIS's ``ModelGateway.generate`` takes a text prompt, but its cloud provider
builds the request as ``_build_messages(prompt, system_prompt, kwargs["messages"])``
and posts the result to an OpenAI-compatible ``/chat/completions``. So a caller
that passes a ready-made ``messages`` list gets it used verbatim, and the
standard ``image_url`` content block travels with no change to the gateway at
all. The frame goes as a ``data:`` URL because the camera is on a home LAN and
no hosted model could fetch a link to it.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

#: Roughly what a provider will accept in one request. Free tiers are the
#: tightest, and the failure — a 413, or a truncated body — reads as a model
#: problem rather than an oversized picture, so it is worth catching early.
MAX_FRAME_BYTES = 4 * 1024 * 1024

SYSTEM_PROMPT = (
    "You are the eyes of a small home robot. You are shown one frame from its camera. "
    "Answer in at most three short sentences, plainly, as if speaking to the person in "
    "front of the robot. Do not describe the image as an image, do not mention framing, "
    "lighting or resolution, and never guess at the identity of a person — if you are "
    "asked about a person, describe only what they are doing or holding. If the frame is "
    "too dark or blurred to tell, say exactly that."
)

#: The question asked when the user did not supply one. Keys are the "kind"
#: a tool passes through from the matched intent.
PROMPTS: Dict[str, str] = {
    "scene": "What do you see in front of you? Name the main things briefly.",
    "object": (
        "What is the object being held up or placed in front of the camera? "
        "Name it as specifically as you can. If there are several, name the "
        "nearest or most prominent one first."
    ),
    "count": "How many distinct objects are in front of the camera? List them.",
    "ripeness": (
        "Name any fruit or vegetable you can see and say whether each looks ripe, "
        "unripe or past its best, giving the visual cue that tells you — colour, "
        "skin texture, bruising or blemishes."
    ),
    "text": (
        "Read out any text, label, screen or writing that is visible. Quote it "
        "exactly. If there is none, say so."
    ),
    "person": (
        "Is there a person in front of the camera? If so, say what they appear to be "
        "doing or holding. Do not speculate about who they are."
    ),
}

DEFAULT_KIND = "scene"


class FrameTooLarge(ValueError):
    """The frame is bigger than a provider will accept in one request."""


def data_url(frame: bytes, mime: str = "image/jpeg") -> str:
    """A frame as a ``data:`` URL, ready to drop into an image content block."""
    if not isinstance(frame, (bytes, bytearray)) or not frame:
        raise ValueError("There is no frame to send.")
    if len(frame) > MAX_FRAME_BYTES:
        raise FrameTooLarge(
            f"That frame is {len(frame) // 1024} KB, over the {MAX_FRAME_BYTES // 1024} KB "
            "a request should carry. Ask the camera for a smaller size."
        )
    encoded = base64.b64encode(bytes(frame)).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def question_for(kind: Optional[str], question: Optional[str] = None) -> str:
    """The question to ask: the user's own words win, else the canned one."""
    if question and str(question).strip():
        return str(question).strip()
    return PROMPTS.get((kind or DEFAULT_KIND).strip().lower(), PROMPTS[DEFAULT_KIND])


def build_vision_messages(
    frame: bytes,
    question: str,
    *,
    system_prompt: Optional[str] = SYSTEM_PROMPT,
    detail: str = "auto",
) -> List[Dict[str, Any]]:
    """An OpenAI-compatible message list carrying the frame and the question.

    One user message with two content parts. The text goes first: models weight
    an instruction ahead of the image more reliably than one trailing it.
    """
    if detail not in ("auto", "low", "high"):
        raise ValueError("detail must be 'auto', 'low' or 'high'.")

    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {
                    "type": "image_url",
                    "image_url": {"url": data_url(frame), "detail": detail},
                },
            ],
        }
    )
    return messages


def estimate_request_bytes(frame: bytes) -> int:
    """Roughly how big the request body will be, base64 overhead included."""
    return (len(frame) * 4 + 2) // 3 + len(SYSTEM_PROMPT) + 512


@dataclass(frozen=True)
class Described:
    """What to say, and what to show."""

    speech: str
    display: str
    question: str
    model: str = ""
    frame_bytes: int = 0


def _sentences(text: str) -> List[str]:
    out: List[str] = []
    current = ""
    for char in text:
        current += char
        if char in ".!?":
            stripped = current.strip()
            if stripped:
                out.append(stripped)
            current = ""
    tail = current.strip()
    if tail:
        out.append(tail)
    return out


def shape_answer(
    text: str,
    question: str,
    *,
    model: str = "",
    frame_bytes: int = 0,
    max_spoken_sentences: int = 2,
) -> Described:
    """Split a model's answer into something speakable and something readable.

    The spoken form is clipped to a couple of sentences because this comes out
    of a small speaker on a robot, where a five-sentence paragraph is not a
    richer answer — it is a wait. The full text is kept for the screen.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return Described(
            speech="I took a look but the model sent nothing back.",
            display="The vision model returned an empty answer.",
            question=question,
            model=model,
            frame_bytes=frame_bytes,
        )

    parts = _sentences(cleaned)
    spoken = " ".join(parts[:max_spoken_sentences]) if parts else cleaned
    return Described(
        speech=spoken,
        display=cleaned,
        question=question,
        model=model,
        frame_bytes=frame_bytes,
    )


def no_vision_model_message() -> str:
    """What to say when there is a camera but nothing to interpret it with.

    Deliberately specific. "I can't do that" sends someone hunting through
    code; naming the setting and where to get a key does not.
    """
    return (
        "I can see — the camera is working — but no vision model is configured, "
        "so I cannot tell you what I am looking at. Set VISION_MODEL in .env to a "
        "model that accepts images (for example a free OpenRouter vision model, or "
        "gemini-flash-latest with GEMINI_API_KEY set) and ask me again."
    )
