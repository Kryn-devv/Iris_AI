"""The robot's eye, brain side.

Fetches frames from the ESP32-CAM, finds and recognises faces locally, and
sends frames to a vision model when the question is about objects rather than
people. Nothing here imports IRIS, and nothing here imports a heavy vision
library at module scope — see ``faces.py`` for why both of those matter.

``iris_integration/`` next door wires this into IRIS as tools.
"""

from .camera import CameraEye, Presence, capture_params, parse_motion
from .errors import (
    BackendMismatch,
    CameraUnreachable,
    NoFaceFound,
    NoFrame,
    RobotEyeError,
    VisionUnavailable,
)
from .faces import (
    BACKENDS,
    Box,
    FaceRecognizer,
    Match,
    module_available,
    select_backend,
)
from .recognize import Sighting, describe_sighting, enroll, look
from .store import FaceStore, Person

__all__ = [
    "BACKENDS",
    "BackendMismatch",
    "Box",
    "CameraEye",
    "CameraUnreachable",
    "FaceRecognizer",
    "FaceStore",
    "Match",
    "NoFaceFound",
    "NoFrame",
    "Person",
    "Presence",
    "RobotEyeError",
    "Sighting",
    "VisionUnavailable",
    "capture_params",
    "describe_sighting",
    "enroll",
    "look",
    "module_available",
    "parse_motion",
    "select_backend",
]
