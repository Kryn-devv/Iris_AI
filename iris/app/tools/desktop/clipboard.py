"""Clipboard read/write tools with layered, cross-platform fallbacks.

Neither tool declares ``required_capabilities`` — clipboard access has too
many viable providers to gate on any single one. Instead the module-level
functions :func:`read_clipboard_text` and :func:`write_clipboard_text`
walk a fallback chain at call time:

1. ``pyperclip`` when importable (it picks the best native backend itself).
2. Platform binaries via subprocess:
   ``wl-paste``/``wl-copy`` (Wayland), ``xclip``, ``xsel`` (X11) on Linux;
   ``pbpaste``/``pbcopy`` on macOS;
   ``powershell -NoProfile Get-Clipboard``/``Set-Clipboard`` on Windows.
3. A clean :class:`ToolError` carrying an install hint when nothing works.

The chain lives in module functions (not methods) so it can be unit-tested
with monkeypatched ``shutil.which``/``subprocess.run``. Reads longer than
:data:`MAX_CLIPBOARD_READ_CHARS` are truncated by the read tool with a note.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, List, Tuple

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import is_linux, is_macos, is_windows, try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.clipboard")

#: Reads longer than this are truncated (with a note) before returning.
MAX_CLIPBOARD_READ_CHARS = 100_000
#: Refuse to place absurdly large payloads on the clipboard.
MAX_CLIPBOARD_WRITE_CHARS = 1_000_000
#: Per-binary timeout; a wedged clipboard helper must not hang the tool.
_SUBPROCESS_TIMEOUT = 10.0


# =============================================================================
# Fallback chain (module-level so tests can monkeypatch shutil/subprocess)
# =============================================================================


def _read_backends() -> List[Tuple[str, List[str]]]:
    """Ordered (name, argv) candidates for reading the clipboard on this OS."""
    if is_windows():
        return [("powershell", ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"])]
    if is_macos():
        return [("pbpaste", ["pbpaste"])]
    return [
        ("wl-paste", ["wl-paste", "--no-newline"]),
        ("xclip", ["xclip", "-selection", "clipboard", "-o"]),
        ("xsel", ["xsel", "--clipboard", "--output"]),
    ]


def _write_backends() -> List[Tuple[str, List[str]]]:
    """Ordered (name, argv) candidates for writing the clipboard on this OS.

    Each command reads the payload from stdin, so no text ever needs to be
    escaped into a command line (the PowerShell variant pipes ``$input``).
    """
    if is_windows():
        return [("powershell", ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"])]
    if is_macos():
        return [("pbcopy", ["pbcopy"])]
    return [
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard", "-i"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
    ]


def _install_hint() -> str:
    """Actionable hint for the current OS when no clipboard provider works."""
    if is_linux():
        return (
            "Install a clipboard provider: 'pip install pyperclip', or the "
            "'wl-clipboard' package (Wayland), or 'xclip'/'xsel' (X11). "
            "A graphical session must be running."
        )
    if is_macos():
        return "Install pyperclip with 'pip install pyperclip' (pbcopy/pbpaste should normally exist)."
    if is_windows():
        return "Install pyperclip with 'pip install pyperclip' (or ensure PowerShell is on PATH)."
    return "Install pyperclip with 'pip install pyperclip'."


def read_clipboard_text() -> str:
    """Return the clipboard's text content, trying every available provider.

    Raises:
        ToolError: when no provider is installed or all of them fail.
    """
    pyperclip = try_import("pyperclip")
    if pyperclip is not None:
        try:
            value = pyperclip.paste()
            if value is not None:
                return str(value)
        except Exception as exc:  # noqa: BLE001 - fall through to binaries
            logger.debug("pyperclip.paste() failed (%s); trying platform binaries.", exc)

    for name, argv in _read_backends():
        if shutil.which(argv[0]) is None:
            continue
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Clipboard read via %s failed: %s", name, exc)
            continue
        if proc.returncode == 0:
            return proc.stdout
        logger.debug(
            "Clipboard read via %s exited %s: %s", name, proc.returncode, proc.stderr.strip()
        )

    raise ToolError(
        "Could not read the clipboard. " + _install_hint(),
        speech="I can't reach the clipboard on this machine.",
    )


def write_clipboard_text(text: str) -> str:
    """Place ``text`` on the clipboard and return the backend name used.

    Raises:
        ToolError: when no provider is installed or all of them fail.
    """
    pyperclip = try_import("pyperclip")
    if pyperclip is not None:
        try:
            pyperclip.copy(text)
            return "pyperclip"
        except Exception as exc:  # noqa: BLE001 - fall through to binaries
            logger.debug("pyperclip.copy() failed (%s); trying platform binaries.", exc)

    for name, argv in _write_backends():
        if shutil.which(argv[0]) is None:
            continue
        try:
            proc = subprocess.run(
                argv, input=text, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Clipboard write via %s failed: %s", name, exc)
            continue
        if proc.returncode == 0:
            return name
        logger.debug(
            "Clipboard write via %s exited %s: %s", name, proc.returncode, proc.stderr.strip()
        )

    raise ToolError(
        "Could not write to the clipboard. " + _install_hint(),
        speech="I can't reach the clipboard on this machine.",
    )


# =============================================================================
# Tools
# =============================================================================


class ClipboardReadTool(BaseTool):
    """Read the current text content of the system clipboard."""

    name = "clipboard_read"
    description = "Reads the current text content of the system clipboard."
    permission_level = PermissionLevel.READ
    category = ToolCategory.DESKTOP
    aliases = ("get_clipboard", "paste_from_clipboard")
    mutating = False
    examples = (
        ToolExample(utterance="what's on my clipboard", arguments={}),
        ToolExample(utterance="read the clipboard aloud", arguments={}),
    )
    input_schema = ToolParameterSchema(properties={}, required=[])

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        text = await self.to_thread(read_clipboard_text)

        length = len(text)
        truncated = length > MAX_CLIPBOARD_READ_CHARS
        note = None
        if truncated:
            text = text[:MAX_CLIPBOARD_READ_CHARS]
            note = (
                f"Clipboard content was {length} characters; "
                f"only the first {MAX_CLIPBOARD_READ_CHARS} are returned."
            )

        if not text.strip():
            speech = "The clipboard is empty."
        elif length <= 120 and not truncated:
            speech = f"The clipboard says: {text.strip()}"
        else:
            speech = f"The clipboard holds {length} characters of text."

        result: Dict[str, Any] = {
            "text": text,
            "length": length,
            "truncated": truncated,
            "speech": speech,
        }
        if note:
            result["note"] = note
            result["display"] = note
        return result


class ClipboardWriteTool(BaseTool):
    """Replace the system clipboard content with the given text."""

    name = "clipboard_write"
    description = "Copies the given text onto the system clipboard, replacing its content."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("set_clipboard", "copy_to_clipboard")
    mutating = True
    examples = (
        ToolExample(
            utterance="copy my email address to the clipboard",
            arguments={"text": "user@example.com"},
        ),
        ToolExample(utterance="clear the clipboard", arguments={"text": ""}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "text": {
                "type": "string",
                "description": "The text to place on the clipboard (empty string clears it).",
            },
        },
        required=["text"],
    )

    async def _run(self, text: Any = None, **kwargs: Any) -> Dict[str, Any]:
        if text is None:
            raise ToolError(
                "Provide the text to place on the clipboard ('text' is required).",
                speech="I need the text you want copied.",
            )
        payload = str(text)
        if len(payload) > MAX_CLIPBOARD_WRITE_CHARS:
            raise ToolError(
                f"Text is too large for the clipboard tool "
                f"({len(payload)} characters; limit {MAX_CLIPBOARD_WRITE_CHARS}).",
                speech="That text is too large to copy.",
            )

        backend = await self.to_thread(write_clipboard_text, payload)

        if payload == "":
            speech = "Cleared the clipboard."
        else:
            speech = f"Copied {len(payload)} character{'s' if len(payload) != 1 else ''} to the clipboard."
        return {"length": len(payload), "backend": backend, "speech": speech}


def get_tools() -> list[BaseTool]:
    return [ClipboardReadTool(), ClipboardWriteTool()]
