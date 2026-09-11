"""Failures the vision code reports, in language a person can act on.

These are deliberately separate from IRIS's own ``ToolError``: this package
knows nothing about IRIS, so it can be unit-tested on a bare Python install
and reused from anything else. ``iris_integration`` translates them at the
boundary.
"""

from __future__ import annotations


class RobotEyeError(RuntimeError):
    """Base class: something about looking at the world did not work."""


class CameraUnreachable(RobotEyeError):
    """The camera did not answer, or answered with an error."""


class NoFrame(RobotEyeError):
    """The camera answered, but not with a usable picture."""


class VisionUnavailable(RobotEyeError):
    """A capability is missing rather than broken.

    Carries the install hint separately so a caller can show "I can see, but I
    can't recognise faces until you install X" instead of a bare traceback.
    """

    def __init__(self, message: str, *, install_hint: str = ""):
        super().__init__(message)
        self.install_hint = install_hint

    def __str__(self) -> str:  # pragma: no cover - formatting only
        base = super().__str__()
        return f"{base} Install with: {self.install_hint}" if self.install_hint else base


class NoFaceFound(RobotEyeError):
    """There was a picture, but nobody in it."""


class BackendMismatch(RobotEyeError):
    """Enrolled face data was produced by a different recogniser.

    Face embeddings are only comparable within the model that made them, so
    this is refused rather than answered with a meaningless distance.
    """
