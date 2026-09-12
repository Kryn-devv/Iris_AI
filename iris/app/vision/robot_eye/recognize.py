"""Putting a frame, a recogniser and the enrolled faces together.

Everything expensive is injected: the recogniser, the JPEG decoder, the clock.
What is left is the part with the judgement in it — how many faces count as an
answer, when a face is too small to be worth guessing about, and what the robot
actually says — and that part is testable without a camera, a model or a GPU.

The phrasing lives here rather than in the IRIS tool layer on purpose. "I can
see someone but they are too far away to make out" is the difference between a
robot that seems to be paying attention and one that just says no, and it
deserves to be tested like any other behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

from .errors import NoFaceFound, VisionUnavailable
from .faces import (
    MIN_USEFUL_FACE_FRACTION,
    Box,
    Match,
    decode_jpeg,
    face_fraction,
    primary_face,
)
from .store import FaceStore, Person

#: Enrolment deserves a better look than recognition does: these samples are
#: what every later answer is measured against, so a face filling less of the
#: frame than this is refused with an ask to come closer rather than stored as
#: a permanently mediocre reference.
MIN_ENROLL_FACE_FRACTION = 0.030


@dataclass(frozen=True)
class SeenFace:
    """One face in the frame, and who it turned out to be."""

    box: Box
    fraction: float
    match: Optional[Match] = None
    too_small: bool = False

    @property
    def name(self) -> Optional[str]:
        return self.match.name if self.match else None


@dataclass(frozen=True)
class Sighting:
    """Everything one look produced."""

    faces: Tuple[SeenFace, ...] = ()
    frame_width: int = 0
    frame_height: int = 0
    backend: str = ""
    #: The face treated as "the person talking to me", if any.
    primary: Optional[SeenFace] = None

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def known(self) -> Tuple[SeenFace, ...]:
        return tuple(face for face in self.faces if face.match is not None)

    @property
    def unknown(self) -> Tuple[SeenFace, ...]:
        return tuple(
            face for face in self.faces if face.match is None and not face.too_small
        )


def look(
    frame: bytes,
    recognizer: Any,
    store: Optional[FaceStore] = None,
    *,
    decode: Callable[[bytes], Any] = decode_jpeg,
    min_fraction: float = MIN_USEFUL_FACE_FRACTION,
) -> Sighting:
    """Find the faces in one frame and match them against the enrolled ones.

    Works without a store, in which case it detects and does not identify —
    which is what "is anyone there?" needs and all a detection-only backend
    can offer anyway.
    """
    image = decode(frame)
    height, width = int(image.shape[0]), int(image.shape[1])

    can_embed = getattr(recognizer, "spec", None) is None or recognizer.spec.can_embed

    if store is None or not can_embed:
        boxes = list(recognizer.detect(image))
        faces = tuple(
            SeenFace(
                box=box,
                fraction=face_fraction(box, width, height),
                match=None,
                too_small=face_fraction(box, width, height) < min_fraction,
            )
            for box in boxes
        )
    else:
        pairs = list(recognizer.embed(image))
        faces_list: List[SeenFace] = []
        for box, embedding in pairs:
            fraction = face_fraction(box, width, height)
            if fraction < min_fraction:
                # Not compared at all: a face this small produces an embedding
                # from a dozen pixels, and the distance it yields is noise that
                # would happily land inside the threshold.
                faces_list.append(SeenFace(box=box, fraction=fraction, too_small=True))
                continue
            match = store.match(
                embedding,
                backend=recognizer.name,
                metric=recognizer.metric,
                threshold=recognizer.threshold,
            )
            faces_list.append(SeenFace(box=box, fraction=fraction, match=match))
        faces = tuple(faces_list)

    chosen: Optional[SeenFace] = None
    if faces:
        biggest = primary_face([face.box for face in faces], width, height)
        for face in faces:
            if face.box == biggest:
                chosen = face
                break

    return Sighting(
        faces=faces,
        frame_width=width,
        frame_height=height,
        backend=getattr(recognizer, "name", ""),
        primary=chosen,
    )


def enroll(
    frame: bytes,
    name: str,
    recognizer: Any,
    store: FaceStore,
    *,
    now: float,
    owner: Optional[bool] = None,
    decode: Callable[[bytes], Any] = decode_jpeg,
) -> Tuple[Person, str]:
    """Take one face sample for someone. Returns the person and what to say.

    Refuses anything ambiguous. An enrolment that quietly stores the wrong
    face, or a bad sample of the right one, is worse than one that fails: the
    mistake is invisible and every later answer inherits it.
    """
    if not getattr(recognizer, "spec", None) or not recognizer.spec.can_embed:
        raise VisionUnavailable(
            "The installed face backend can find faces but not tell them apart, "
            "so there is nothing to remember yet.",
            install_hint="pip install face-recognition",
        )

    image = decode(frame)
    height, width = int(image.shape[0]), int(image.shape[1])
    pairs = list(recognizer.embed(image))

    if not pairs:
        raise NoFaceFound(
            "I can't see a face in that. Look straight at the camera, "
            "with the light on your face rather than behind you."
        )

    if len(pairs) > 1:
        raise NoFaceFound(
            f"I can see {len(pairs)} faces, so I don't know which one is {name}. "
            "Let me look at one person at a time."
        )

    box, embedding = pairs[0]
    fraction = face_fraction(box, width, height)
    if fraction < MIN_ENROLL_FACE_FRACTION:
        raise NoFaceFound(
            "You're a bit far away for me to remember your face properly — "
            "come closer, until your face fills a good part of the view, and I'll try again."
        )

    person = store.enroll(
        name,
        embedding,
        backend=recognizer.name,
        metric=recognizer.metric,
        now=now,
        owner=owner,
    )

    if person.sample_count == 1:
        speech = (
            f"Got it — I'll remember that face as {person.name}. "
            "Show me once or twice more, in different light, and I'll be steadier about it."
        )
    else:
        speech = f"Noted, that's {person.sample_count} looks at {person.name} now."
    return person, speech


# ------------------------------------------------------------------ phrasing

def _join(names: Sequence[str]) -> str:
    names = list(names)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def describe_sighting(sighting: Sighting, store: Optional[FaceStore] = None) -> str:
    """One sentence about who the robot is looking at.

    The distinctions matter: nobody there, somebody too far away to identify,
    somebody it does not know, and you. Collapsing the middle two into "I don't
    recognise you" makes the robot look broken when it is merely far away.
    """
    if sighting.face_count == 0:
        return "I don't see anyone in front of me."

    too_small = [face for face in sighting.faces if face.too_small]
    if len(too_small) == sighting.face_count:
        if sighting.face_count == 1:
            return "There's someone there, but too far away for me to make out who."
        return f"I can see {sighting.face_count} people, but all too far away to make out."

    owner = store.owner() if store is not None else None
    owner_name = owner.name if owner else None

    named: List[str] = []
    for face in sighting.known:
        name = face.name or ""
        named.append("you" if owner_name and name == owner_name else name)

    unknown_count = len(sighting.unknown)

    if named and not unknown_count:
        if len(named) == 1:
            only = named[0]
            return "Yes, that's you." if only == "you" else f"That's {only}."
        return f"I can see {_join(named)}."

    if named and unknown_count:
        others = "someone I don't know" if unknown_count == 1 else f"{unknown_count} people I don't know"
        return f"I can see {_join(named)}, and {others}."

    if unknown_count == 1:
        if store is not None and len(store) == 0:
            return (
                "There's someone in front of me, but I haven't been introduced to anybody yet. "
                "Say 'remember my face as <your name>' and I'll learn it."
            )
        return "There's someone there, but I don't recognise them."
    return f"I can see {unknown_count} people, none of whom I recognise."


def describe_presence_only(sighting: Sighting) -> str:
    """Wording for a detection-only backend, which cannot name anyone."""
    if sighting.face_count == 0:
        return "I don't see anyone in front of me."
    if sighting.face_count == 1:
        return (
            "There's one person in front of me. I can see a face but I can't tell "
            "whose it is — that needs a face recogniser installed."
        )
    return (
        f"I can see {sighting.face_count} people. I can't tell who they are — "
        "that needs a face recogniser installed."
    )


def is_owner(sighting: Sighting, store: FaceStore) -> bool:
    """True when the person the robot is facing is the one it belongs to."""
    owner = store.owner()
    if owner is None or sighting.primary is None:
        return False
    return sighting.primary.name == owner.name
