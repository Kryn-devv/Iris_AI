"""Full-screen screenshot capture with a layered cross-platform backend chain.

The tool declares no ``required_capabilities`` — screen capture has too many
viable providers to gate on any single one. :func:`capture_screenshot` walks
a fallback chain at call time:

1. ``mss`` when importable (fast, pure-Python, all platforms).
2. ``PIL.ImageGrab`` on Windows/macOS (part of Pillow).
3. Platform binaries via subprocess on Linux: ``scrot``,
   ``gnome-screenshot -f``, ``spectacle -b -n -o``.
4. The built-in ``screencapture -x`` utility on macOS.
5. A clean :class:`ToolError` carrying an install hint when nothing works.

Images are saved as PNG into :func:`iris.app.core.paths.screenshots_dir`
under a timestamped name (``time.strftime``) unless the caller supplies a
``filename``, which is reduced to a safe basename by
:func:`sanitize_filename` (no directory traversal, no shell metacharacters,
always a single ``.png`` extension). The final path is still validated by the
filesystem sandbox before anything is written.

The chain lives in module functions (not methods) so tests can monkeypatch
``try_import``, ``shutil.which`` and ``subprocess.run``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.app.core import paths
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import is_linux, is_macos, is_windows, try_import
from iris.app.core.security import PermissionLevel, SandboxError, default_path_sandbox
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.screenshot")

#: Per-binary timeout; a wedged capture helper must not hang the tool.
_SUBPROCESS_TIMEOUT = 15.0

#: Characters allowed in a user-supplied screenshot filename.
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._ \-]+")

#: Maximum stem length kept from a user-supplied filename.
_MAX_STEM_CHARS = 120


# =============================================================================
# Filename handling (pure logic, unit-testable)
# =============================================================================


def sanitize_filename(filename: Optional[str]) -> str:
    """Reduce a user-supplied filename to a safe PNG basename.

    * Directory components (``/`` and ``\\``) are stripped, so a value like
      ``../../etc/passwd`` becomes ``passwd.png`` — no traversal is possible.
    * Characters outside ``[A-Za-z0-9._ -]`` are replaced with underscores.
    * A single ``.png`` extension is guaranteed (an existing one is not doubled).
    * When nothing usable remains (or no name was given), a timestamped name
      like ``screenshot_2026-08-25_14-30-59.png`` is generated instead.
    """
    raw = (filename or "").strip()
    base = Path(raw.replace("\\", "/")).name if raw else ""
    base = _UNSAFE_CHARS_RE.sub("_", base)
    if base.lower().endswith(".png"):
        base = base[: -len(".png")]
    base = base.strip(" ._")
    if not base:
        base = time.strftime("screenshot_%Y-%m-%d_%H-%M-%S")
    return base[:_MAX_STEM_CHARS] + ".png"


def target_path(filename: Optional[str]) -> Path:
    """Resolve the sandboxed absolute path a screenshot will be written to."""
    directory = paths.screenshots_dir()
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / sanitize_filename(filename)
    try:
        return default_path_sandbox.resolve(candidate)
    except SandboxError as exc:
        raise ToolError(
            f"The screenshots folder is outside the allowed workspace: {exc}",
            speech="I'm not allowed to write screenshots there.",
        ) from exc


# =============================================================================
# Capture chain (module-level so tests can monkeypatch its collaborators)
# =============================================================================


def _install_hint() -> str:
    """Actionable hint for the current OS when no capture provider works."""
    if is_linux():
        return (
            "Install a screenshot provider: 'pip install mss', or one of the scrot "
            "('sudo apt install scrot'), gnome-screenshot or spectacle packages. "
            "A graphical session must be running."
        )
    if is_macos():
        return "Install mss with 'pip install mss' (the built-in 'screencapture' should also work)."
    if is_windows():
        return "Install a capture library with 'pip install mss' or 'pip install pillow'."
    return "Install mss with 'pip install mss'."


def _linux_capture_backends(path: Path) -> List[tuple[str, List[str]]]:
    """Ordered (name, argv) subprocess candidates for capturing on Linux."""
    return [
        ("scrot", ["scrot", str(path)]),
        ("gnome-screenshot", ["gnome-screenshot", "-f", str(path)]),
        ("spectacle", ["spectacle", "-b", "-n", "-o", str(path)]),
    ]


def capture_screenshot(path: Path) -> str:
    """Capture the full screen to ``path`` as PNG, returning the backend used.

    Walks the mss -> Pillow -> platform-binary chain described in the module
    docstring, collecting per-backend failure details for the final error.

    Raises:
        ToolError: when no provider is installed or all of them fail.
    """
    path = Path(path)
    path.unlink(missing_ok=True)  # older scrot refuses to overwrite
    errors: List[str] = []

    mss_module = try_import("mss")
    if mss_module is not None:
        try:
            with mss_module.mss() as sct:
                sct.shot(mon=-1, output=str(path))
            return "mss"
        except Exception as exc:  # noqa: BLE001 - fall through to next backend
            errors.append(f"mss: {exc}")
            logger.debug("mss capture failed (%s); trying the next backend.", exc)

    if is_windows() or is_macos():
        image_grab = try_import("PIL.ImageGrab")
        if image_grab is not None:
            try:
                if is_windows():
                    image = image_grab.grab(all_screens=True)
                else:
                    image = image_grab.grab()
                image.save(str(path), "PNG")
                return "pillow"
            except Exception as exc:  # noqa: BLE001 - fall through to next backend
                errors.append(f"pillow: {exc}")
                logger.debug("Pillow capture failed (%s); trying the next backend.", exc)

    if is_linux():
        for name, argv in _linux_capture_backends(path):
            if shutil.which(name) is None:
                continue
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT
                )
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"{name}: {exc}")
                continue
            if proc.returncode == 0 and path.exists():
                return name
            detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
            errors.append(f"{name}: {detail}")

    if is_macos() and shutil.which("screencapture"):
        argv = ["screencapture", "-x", str(path)]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)
            if proc.returncode == 0 and path.exists():
                return "screencapture"
            detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
            errors.append(f"screencapture: {detail}")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"screencapture: {exc}")

    detail = f" Attempted: {'; '.join(errors)}." if errors else ""
    raise ToolError(
        f"Could not capture the screen. {_install_hint()}{detail}",
        speech="I couldn't take a screenshot on this machine.",
    )


# =============================================================================
# Tool
# =============================================================================


class ScreenshotTool(BaseTool):
    """Capture the full screen to a PNG file in the IRIS screenshots folder."""

    name = "take_screenshot"
    description = "Captures the full screen to a PNG file in the IRIS screenshots folder."
    permission_level = PermissionLevel.READ  # it only reads the screen
    category = ToolCategory.DESKTOP
    aliases = ("screenshot", "capture_screen", "print_screen")
    mutating = False
    examples = (
        ToolExample(utterance="take a screenshot", arguments={}),
        ToolExample(utterance="screenshot the screen as before-update",
                    arguments={"filename": "before-update"}),
        ToolExample(utterance="capture my screen", arguments={}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "filename": {
                "type": "string",
                "description": (
                    "Optional file name (no directories); a '.png' extension is added "
                    "automatically. Defaults to a timestamped name."
                ),
            },
        },
        required=[],
    )

    async def _run(self, filename: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        path = target_path(filename)
        backend = await self.to_thread(capture_screenshot, path)
        if not path.exists():
            raise ToolError(
                f"The {backend} backend reported success but produced no file at {path}.",
                speech="The screenshot didn't come out.",
            )

        size_bytes = path.stat().st_size
        logger.info("Captured screenshot to %s via %s (%d bytes).", path, backend, size_bytes)
        return {
            "file": str(path),
            "backend": backend,
            "bytes": size_bytes,
            "speech": "Screenshot saved.",
            "display": f"Screenshot saved to {path} (via {backend}).",
            "artifacts": [str(path)],
            "ui": {"preview": str(path)},
        }


def get_tools() -> list[BaseTool]:
    return [ScreenshotTool()]
