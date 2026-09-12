"""Finding faces in a frame, and deciding whose they are.

WHY THIS RUNS HERE AND NOT ON THE CAMERA
The AI-Thinker ESP32-CAM cannot do it. Espressif's face pipeline (ESP-WHO /
ESP-DL) no longer supports the original ESP32 — it targets the S3 and P4 — and
the Arduino core dropped the old ``fd_forward.h`` / ``fr_forward.h`` headers
after 3.0.7. So the camera sends pixels and this decides who is in them.

TWO SEPARATE QUESTIONS
Detecting a face ("somebody is there") and recognising one ("that is Prakash")
need different amounts of machinery, so they degrade separately. With only
OpenCV installed the robot can still tell you someone is in front of it; it
takes a real embedding model to tell you who.

NO TOP-LEVEL THIRD-PARTY IMPORTS
This module imports on a bare Python install, because IRIS must boot and pass
its tests on headless Linux with no optional dependencies present. Every heavy
import is inside the function that needs it, and the comparison maths below is
plain Python over sequences of floats — with a handful of enrolled people that
is far quicker than the frame it just decoded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .errors import NoFaceFound, VisionUnavailable

Vector = Sequence[float]

EUCLIDEAN = "euclidean"
COSINE = "cosine"


@dataclass(frozen=True)
class Box:
    """A face's position in the frame, in pixels."""

    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def as_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True)
class Match:
    """Who a face looks like, and how sure we are."""

    name: str
    distance: float
    confidence: float
    threshold: float
    metric: str

    @property
    def is_match(self) -> bool:
        return self.distance <= self.threshold


# ---------------------------------------------------------------- backends

@dataclass(frozen=True)
class BackendSpec:
    """One way of recognising faces, and what it needs installed."""

    name: str
    modules: Tuple[str, ...]
    metric: str
    #: Distance at or below which two faces are the same person. Chosen a
    #: little stricter than each library's own default: for a robot that
    #: greets you by name, mistaking a stranger for you is a worse failure
    #: than occasionally asking you to step closer.
    threshold: float
    can_embed: bool
    install_hint: str
    note: str = ""


#: In preference order. ``face_recognition`` first because its distances are
#: the best understood of the three and it needs no model download at runtime.
BACKENDS: Tuple[BackendSpec, ...] = (
    BackendSpec(
        name="face_recognition",
        modules=("face_recognition", "numpy"),
        metric=EUCLIDEAN,
        threshold=0.55,
        can_embed=True,
        install_hint="pip install face-recognition",
        note="dlib, 128-d embeddings. Needs a C++ toolchain to build.",
    ),
    BackendSpec(
        name="insightface",
        modules=("insightface", "numpy"),
        metric=COSINE,
        threshold=0.55,
        can_embed=True,
        install_hint="pip install insightface onnxruntime",
        note="ArcFace, 512-d embeddings. Downloads a model on first use.",
    ),
    BackendSpec(
        name="opencv",
        modules=("cv2", "numpy"),
        metric=EUCLIDEAN,
        threshold=0.0,
        can_embed=False,
        install_hint="pip install opencv-python-headless",
        note="Detection only: finds faces, cannot tell you whose they are.",
    ),
)

BACKENDS_BY_NAME: Dict[str, BackendSpec] = {spec.name: spec for spec in BACKENDS}


def select_backend(
    is_available: Callable[[str], bool],
    preference: Optional[str] = None,
    *,
    need_embeddings: bool = True,
) -> BackendSpec:
    """Pick the best installed backend, or explain what to install.

    ``is_available(module_name)`` decides whether an import would succeed;
    injecting it keeps this testable without installing anything.
    """
    if preference is not None:
        spec = BACKENDS_BY_NAME.get(preference)
        if spec is None:
            known = ", ".join(BACKENDS_BY_NAME)
            raise VisionUnavailable(f"'{preference}' is not a face backend I know ({known}).")
        if need_embeddings and not spec.can_embed:
            raise VisionUnavailable(
                f"'{spec.name}' can find faces but not recognise them. {spec.note}"
            )
        if not all(is_available(module) for module in spec.modules):
            raise VisionUnavailable(
                f"The '{spec.name}' face backend is not installed.",
                install_hint=spec.install_hint,
            )
        return spec

    usable = [
        spec
        for spec in BACKENDS
        if (spec.can_embed or not need_embeddings)
        and all(is_available(module) for module in spec.modules)
    ]
    if usable:
        return usable[0]

    wanted = [spec for spec in BACKENDS if spec.can_embed or not need_embeddings]
    options = "; ".join(f"{spec.install_hint} ({spec.note})" for spec in wanted)
    what = "recognise faces" if need_embeddings else "find faces"
    raise VisionUnavailable(
        f"Nothing installed that can {what}.", install_hint=options
    )


def module_available(name: str) -> bool:
    """Whether importing ``name`` would work, without importing it for real."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------- the maths

def l2_norm(vector: Vector) -> float:
    return math.sqrt(sum(float(v) * float(v) for v in vector))


def l2_normalize(vector: Vector) -> Tuple[float, ...]:
    """Scale to unit length. A zero vector is returned unchanged."""
    norm = l2_norm(vector)
    if norm == 0.0:
        return tuple(float(v) for v in vector)
    return tuple(float(v) / norm for v in vector)


def euclidean_distance(a: Vector, b: Vector) -> float:
    if len(a) != len(b):
        raise ValueError(f"Cannot compare a {len(a)}-d face with a {len(b)}-d one.")
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def cosine_distance(a: Vector, b: Vector) -> float:
    """1 - cosine similarity, so that smaller always means more alike.

    Range 0..2. Two identical directions give 0; opposite ones give 2.
    """
    if len(a) != len(b):
        raise ValueError(f"Cannot compare a {len(a)}-d face with a {len(b)}-d one.")
    na, nb = l2_norm(a), l2_norm(b)
    if na == 0.0 or nb == 0.0:
        return 1.0     # no information: exactly undecided
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    similarity = dot / (na * nb)
    # Guard the float error that can push an identical pair to 1.0000000002.
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity


def distance(a: Vector, b: Vector, metric: str) -> float:
    if metric == EUCLIDEAN:
        return euclidean_distance(a, b)
    if metric == COSINE:
        return cosine_distance(a, b)
    raise ValueError(f"Unknown distance metric '{metric}'.")


def confidence_from_distance(dist: float, threshold: float) -> float:
    """A 0..1 number to say out loud, derived from the raw distance.

    Anchored so that a face exactly on the threshold reads as 0.5 — an honest
    "could be" — and falls to 0 at twice the threshold. This is a presentation
    device, not a calibrated probability, and the docstring says so because
    "87% sure" invites being believed more than it deserves.
    """
    if threshold <= 0:
        return 0.0
    value = 1.0 - (float(dist) / (2.0 * float(threshold)))
    return max(0.0, min(1.0, value))


def merge_embeddings(vectors: Sequence[Vector], metric: str) -> Tuple[float, ...]:
    """One representative vector for several samples of the same face.

    Averaging is the right move for both metrics, but for cosine the samples
    are normalized first so a brighter photo — which comes back as a longer
    vector — does not get a bigger vote than a dim one.
    """
    if not vectors:
        raise ValueError("No embeddings to merge.")
    width = len(vectors[0])
    for vector in vectors:
        if len(vector) != width:
            raise ValueError("Cannot merge embeddings of different lengths.")

    samples = [l2_normalize(v) for v in vectors] if metric == COSINE else \
              [tuple(float(x) for x in v) for v in vectors]

    count = float(len(samples))
    averaged = tuple(sum(sample[i] for sample in samples) / count for i in range(width))
    return l2_normalize(averaged) if metric == COSINE else averaged


def best_match(
    embedding: Vector,
    candidates: Iterable[Tuple[str, Vector]],
    metric: str,
    threshold: float,
) -> Optional[Match]:
    """Closest enrolled person within the threshold, or ``None``.

    Every candidate is measured, not just the first under the threshold: with
    two similar-looking people enrolled, "closest" and "first acceptable" are
    different answers and only one of them is right.
    """
    best: Optional[Match] = None
    for name, candidate in candidates:
        try:
            dist = distance(embedding, candidate, metric)
        except ValueError:
            # A stored face of a different width — a different model produced
            # it. Skipped rather than crashing the whole comparison.
            continue
        if best is None or dist < best.distance:
            best = Match(
                name=name,
                distance=dist,
                confidence=confidence_from_distance(dist, threshold),
                threshold=threshold,
                metric=metric,
            )
    if best is None or not best.is_match:
        return None
    return best


def primary_face(boxes: Sequence[Box], frame_width: int, frame_height: int) -> Box:
    """The face to treat as "the person talking to me".

    Biggest wins, because the nearest face is the one addressing the robot;
    ties break toward the middle of the frame. Without this, a photograph on
    the wall behind you can answer "who am I".
    """
    if not boxes:
        raise NoFaceFound("No face in the frame.")

    cx, cy = frame_width / 2.0, frame_height / 2.0

    def rank(box: Box) -> Tuple[int, float]:
        bx, by = box.center
        offset = math.hypot(bx - cx, by - cy)
        return (-box.area, offset)

    return sorted(boxes, key=rank)[0]


def face_fraction(box: Box, frame_width: int, frame_height: int) -> float:
    """How much of the frame the face fills, 0..1.

    Recognition quality tracks this closely, so it is what turns an unhelpful
    "I don't recognise you" into "come a bit closer".
    """
    total = float(max(1, frame_width) * max(1, frame_height))
    return max(0.0, min(1.0, box.area / total))


#: Below this the face is a handful of pixels wide and any answer about whose
#: it is would be a guess. Roughly a face at 3-4 m on an XGA frame.
MIN_USEFUL_FACE_FRACTION = 0.010


# ---------------------------------------------------------------- decoding

def decode_jpeg(data: bytes) -> Any:
    """JPEG bytes to an RGB ``numpy`` array, via whichever library is present."""
    if module_available("PIL"):
        import io

        import numpy
        from PIL import Image

        with Image.open(io.BytesIO(bytes(data))) as image:
            return numpy.asarray(image.convert("RGB"))

    if module_available("cv2"):
        import cv2
        import numpy

        buffer = numpy.frombuffer(bytes(data), dtype=numpy.uint8)
        decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if decoded is None:
            raise VisionUnavailable("That frame would not decode as a JPEG.")
        return cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)

    raise VisionUnavailable(
        "Nothing installed that can open a JPEG.",
        install_hint="pip install pillow  (or opencv-python-headless)",
    )


# ---------------------------------------------------------------- recognisers

class FaceRecognizer:
    """Detection and embedding through whichever backend is installed.

    Constructed with a :class:`BackendSpec`; the heavy library is imported on
    first use rather than at construction, so building one of these in a tool's
    ``__init__`` cannot slow down IRIS's boot or fail it.
    """

    def __init__(self, spec: BackendSpec):
        self.spec = spec
        self._impl: Any = None

    @property
    def metric(self) -> str:
        return self.spec.metric

    @property
    def threshold(self) -> float:
        return self.spec.threshold

    @property
    def name(self) -> str:
        return self.spec.name

    # -- detection -------------------------------------------------------
    def detect(self, image: Any) -> List[Box]:
        if self.spec.name == "face_recognition":
            return self._detect_face_recognition(image)
        if self.spec.name == "insightface":
            return [box for box, _ in self._analyse_insightface(image)]
        if self.spec.name == "opencv":
            return self._detect_opencv(image)
        raise VisionUnavailable(f"Backend '{self.spec.name}' cannot detect faces.")

    def _detect_face_recognition(self, image: Any) -> List[Box]:
        import face_recognition

        # (top, right, bottom, left), which is not the order anything else uses.
        found = face_recognition.face_locations(image)
        return [Box(x=left, y=top, w=right - left, h=bottom - top)
                for (top, right, bottom, left) in found]

    def _detect_opencv(self, image: Any) -> List[Box]:
        import cv2

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            raise VisionUnavailable(
                "OpenCV is installed but its face cascade file is missing.",
                install_hint="pip install --force-reinstall opencv-python-headless",
            )
        grey = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        grey = cv2.equalizeHist(grey)   # helps a lot under a single room lamp
        found = cascade.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(48, 48))
        return [Box(x=int(x), y=int(y), w=int(w), h=int(h)) for (x, y, w, h) in found]

    # -- embedding -------------------------------------------------------
    def embed(self, image: Any, boxes: Optional[Sequence[Box]] = None) -> List[Tuple[Box, Tuple[float, ...]]]:
        """Faces in the image paired with their embeddings.

        Returns plain tuples of floats, not model-specific arrays, so nothing
        downstream — the store, the JSON on disk, the comparison maths — has to
        know or care which library produced them.
        """
        if not self.spec.can_embed:
            raise VisionUnavailable(
                f"'{self.spec.name}' finds faces but cannot recognise them. {self.spec.note}",
                install_hint=BACKENDS_BY_NAME["face_recognition"].install_hint,
            )
        if self.spec.name == "face_recognition":
            return self._embed_face_recognition(image, boxes)
        if self.spec.name == "insightface":
            return self._analyse_insightface(image)
        raise VisionUnavailable(f"Backend '{self.spec.name}' cannot embed faces.")

    def _embed_face_recognition(
        self, image: Any, boxes: Optional[Sequence[Box]]
    ) -> List[Tuple[Box, Tuple[float, ...]]]:
        import face_recognition

        if boxes:
            locations = [(b.y, b.x + b.w, b.y + b.h, b.x) for b in boxes]
        else:
            locations = face_recognition.face_locations(image)
        if not locations:
            return []
        encodings = face_recognition.face_encodings(image, known_face_locations=locations)
        out: List[Tuple[Box, Tuple[float, ...]]] = []
        for (top, right, bottom, left), encoding in zip(locations, encodings):
            box = Box(x=left, y=top, w=right - left, h=bottom - top)
            out.append((box, tuple(float(v) for v in encoding)))
        return out

    def _analyse_insightface(self, image: Any) -> List[Tuple[Box, Tuple[float, ...]]]:
        if self._impl is None:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name="buffalo_l")
            app.prepare(ctx_id=-1, det_size=(640, 640))   # ctx_id -1 = CPU
            self._impl = app

        faces = self._impl.get(image)
        out: List[Tuple[Box, Tuple[float, ...]]] = []
        for face in faces:
            x1, y1, x2, y2 = (int(v) for v in face.bbox)
            box = Box(x=x1, y=y1, w=x2 - x1, h=y2 - y1)
            vector = getattr(face, "normed_embedding", None)
            if vector is None:
                vector = face.embedding
            out.append((box, tuple(float(v) for v in vector)))
        return out
