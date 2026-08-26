"""Window management tools (list / focus / minimize / maximize / close).

No tool here declares ``required_capabilities`` — window control has several
viable providers per platform, so availability is decided at call time by a
small strategy object instead of an import-time gate:

* :class:`PyGetWindowBackend` — the ``pygetwindow`` package, consulted on
  Windows only (its non-Windows ports are broken or read-only, so other
  platforms never use it even when it imports).
* :class:`WmctrlBackend` — the ``wmctrl`` binary on Linux/X11, addressing
  windows by id from ``wmctrl -l`` (with ``xdotool windowminimize`` as the
  minimize helper when present, since wmctrl cannot iconify).
* :class:`XdotoolBackend` — pure ``xdotool`` on Linux when wmctrl is absent.
* :class:`AppleScriptBackend` — ``osascript`` + System Events on macOS
  (requires the Accessibility permission for IRIS/terminal).

:func:`select_backend` walks that chain and raises a helpful
:class:`ToolError` (e.g. "install wmctrl") when nothing is available. Tests
monkeypatch ``select_backend`` with a fake backend, or drive the concrete
backends with monkeypatched ``shutil.which``/``subprocess.run``.

Title matching is case-insensitive substring matching via the pure function
:func:`best_match` (exact > prefix > substring; earliest hit and shortest
title win ties), so "focus firefox" finds "Mozilla Firefox".
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import is_linux, is_macos, is_windows, try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.windows")

#: Per-command timeout; a wedged window manager must not hang the tool.
_SUBPROCESS_TIMEOUT = 10.0


# =============================================================================
# Pure matching logic (unit-testable without any backend)
# =============================================================================


def best_match(titles: Sequence[str], query: str) -> Optional[str]:
    """Pick the open-window title that best matches a partial ``query``.

    Matching is case-insensitive substring containment, ranked so that an
    exact title beats a prefix match, which beats an interior match; ties are
    broken by the earliest match position, then the shortest title. Returns
    ``None`` when nothing contains the query.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return None

    best_key: Optional[Tuple[int, int, int]] = None
    best_title: Optional[str] = None
    for title in titles:
        haystack = (title or "").strip().lower()
        if not haystack:
            continue
        index = haystack.find(needle)
        if index < 0:
            continue
        if haystack == needle:
            rank = 0
        elif index == 0:
            rank = 1
        else:
            rank = 2
        key = (rank, index, len(haystack))
        if best_key is None or key < best_key:
            best_key = key
            best_title = title
    return best_title


# =============================================================================
# Backend strategy objects
# =============================================================================


def _run_command(argv: List[str]) -> "subprocess.CompletedProcess[str]":
    """Run a window-manager helper, converting launch failures to ToolError."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(
            f"'{argv[0]}' could not be run: {exc}",
            speech="The window manager helper failed to run.",
        ) from exc


def _check(proc: "subprocess.CompletedProcess[str]", argv: List[str]) -> str:
    """Raise a clean ToolError when a helper exited non-zero, else stdout."""
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or (proc.stdout or "").strip() or "unknown error"
        raise ToolError(
            f"'{' '.join(argv[:2])}' failed: {detail}",
            speech="The window manager refused that action.",
        )
    return proc.stdout or ""


class WindowBackend:
    """Strategy interface: one concrete backend per window-control provider.

    ``title`` arguments to the action methods are *exact* titles previously
    returned by :meth:`list_titles` (tools resolve fuzzy input first via
    :func:`best_match`).
    """

    name = "abstract"

    def list_titles(self) -> List[str]:
        """Titles of all open, user-visible windows."""
        raise NotImplementedError

    def activate(self, title: str) -> None:
        """Bring the window to the foreground and give it focus."""
        raise NotImplementedError

    def minimize(self, title: str) -> None:
        """Iconify / hide the window."""
        raise NotImplementedError

    def maximize(self, title: str) -> None:
        """Expand the window to fill the screen."""
        raise NotImplementedError

    def close(self, title: str) -> None:
        """Ask the window to close gracefully (like clicking its X button)."""
        raise NotImplementedError


class PyGetWindowBackend(WindowBackend):
    """Backend over the ``pygetwindow`` package (the Windows-native path)."""

    name = "pygetwindow"

    def __init__(self, module: Any):
        self._gw = module

    def list_titles(self) -> List[str]:
        return [t for t in self._gw.getAllTitles() if t and t.strip()]

    def _window(self, title: str) -> Any:
        windows = self._gw.getWindowsWithTitle(title)
        for win in windows:
            if getattr(win, "title", None) == title:
                return win
        if windows:
            return windows[0]
        raise ToolError(
            f"The window titled '{title}' is no longer open.",
            speech="That window seems to have gone away.",
        )

    def activate(self, title: str) -> None:
        win = self._window(title)
        if getattr(win, "isMinimized", False):
            win.restore()
        win.activate()

    def minimize(self, title: str) -> None:
        self._window(title).minimize()

    def maximize(self, title: str) -> None:
        self._window(title).maximize()

    def close(self, title: str) -> None:
        self._window(title).close()


class WmctrlBackend(WindowBackend):
    """Backend over the ``wmctrl`` binary (Linux / X11, EWMH-compliant WMs)."""

    name = "wmctrl"

    def enumerate(self) -> List[Tuple[str, str]]:
        """Return ``(window_id, title)`` pairs from ``wmctrl -l``.

        Lines look like ``0x04800003  0 host Mozilla Firefox``; a desktop
        number of ``-1`` marks docks/panels/the desktop itself and is skipped.
        """
        argv = ["wmctrl", "-l"]
        output = _check(_run_command(argv), argv)
        windows: List[Tuple[str, str]] = []
        for line in output.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            window_id, desktop, _host, title = parts
            if desktop == "-1":
                continue
            title = title.strip()
            if title:
                windows.append((window_id, title))
        return windows

    def list_titles(self) -> List[str]:
        return [title for _id, title in self.enumerate()]

    def _window_id(self, title: str) -> str:
        for window_id, candidate in self.enumerate():
            if candidate == title:
                return window_id
        raise ToolError(
            f"The window titled '{title}' is no longer open.",
            speech="That window seems to have gone away.",
        )

    def _act(self, argv: List[str]) -> None:
        _check(_run_command(argv), argv)

    def activate(self, title: str) -> None:
        self._act(["wmctrl", "-i", "-a", self._window_id(title)])

    def minimize(self, title: str) -> None:
        window_id = self._window_id(title)
        # wmctrl has no iconify verb; xdotool does it properly when present.
        if shutil.which("xdotool"):
            self._act(["xdotool", "windowminimize", window_id])
        else:
            self._act(["wmctrl", "-i", "-r", window_id, "-b", "add,hidden"])

    def maximize(self, title: str) -> None:
        window_id = self._window_id(title)
        self._act(["wmctrl", "-i", "-r", window_id, "-b", "add,maximized_vert,maximized_horz"])

    def close(self, title: str) -> None:
        self._act(["wmctrl", "-i", "-c", self._window_id(title)])


class XdotoolBackend(WindowBackend):
    """Backend over the ``xdotool`` binary (Linux / X11, when wmctrl is absent)."""

    name = "xdotool"

    def _search_ids(self) -> List[str]:
        proc = _run_command(["xdotool", "search", "--onlyvisible", "--name", "."])
        # xdotool exits non-zero when nothing matches; treat that as "no windows".
        if proc.returncode != 0:
            return []
        return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]

    def enumerate(self) -> List[Tuple[str, str]]:
        windows: List[Tuple[str, str]] = []
        for window_id in self._search_ids():
            proc = _run_command(["xdotool", "getwindowname", window_id])
            if proc.returncode != 0:
                continue
            title = (proc.stdout or "").strip()
            if title:
                windows.append((window_id, title))
        return windows

    def list_titles(self) -> List[str]:
        return [title for _id, title in self.enumerate()]

    def _window_id(self, title: str) -> str:
        for window_id, candidate in self.enumerate():
            if candidate == title:
                return window_id
        raise ToolError(
            f"The window titled '{title}' is no longer open.",
            speech="That window seems to have gone away.",
        )

    def _act(self, argv: List[str]) -> None:
        _check(_run_command(argv), argv)

    def activate(self, title: str) -> None:
        self._act(["xdotool", "windowactivate", self._window_id(title)])

    def minimize(self, title: str) -> None:
        self._act(["xdotool", "windowminimize", self._window_id(title)])

    def maximize(self, title: str) -> None:
        window_id = self._window_id(title)
        self._act(
            ["xdotool", "windowstate", "--add", "MAXIMIZED_VERT", "--add", "MAXIMIZED_HORZ",
             window_id]
        )

    def close(self, title: str) -> None:
        self._act(["xdotool", "windowclose", self._window_id(title)])


def _escape_applescript(text: str) -> str:
    """Escape a string for embedding inside AppleScript double quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class AppleScriptBackend(WindowBackend):
    """Backend over ``osascript`` + System Events (macOS).

    Requires the Accessibility permission (System Settings > Privacy &
    Security > Accessibility) for the process hosting IRIS.
    """

    name = "applescript"

    def _osascript(self, script: str) -> str:
        argv = ["osascript", "-e", script]
        proc = _run_command(argv)
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() or "unknown AppleScript error"
            raise ToolError(
                f"AppleScript failed: {detail}. IRIS may need the Accessibility permission "
                "(System Settings > Privacy & Security > Accessibility).",
                speech="macOS blocked that window action.",
            )
        return proc.stdout or ""

    def list_titles(self) -> List[str]:
        script = (
            'set out to ""\n'
            'tell application "System Events"\n'
            "  repeat with proc in (every application process whose visible is true)\n"
            "    repeat with w in (every window of proc)\n"
            "      set out to out & (name of w) & linefeed\n"
            "    end repeat\n"
            "  end repeat\n"
            "end tell\n"
            "return out"
        )
        output = self._osascript(script)
        return [line.strip() for line in output.splitlines() if line.strip()]

    def _act(self, title: str, body: str) -> None:
        escaped = _escape_applescript(title)
        script = (
            'tell application "System Events"\n'
            "  repeat with proc in (every application process whose visible is true)\n"
            "    repeat with w in (every window of proc)\n"
            f'      if name of w is "{escaped}" then\n'
            f"        {body}\n"
            '        return "ok"\n'
            "      end if\n"
            "    end repeat\n"
            "  end repeat\n"
            "end tell\n"
            'return "not found"'
        )
        if self._osascript(script).strip() != "ok":
            raise ToolError(
                f"The window titled '{title}' is no longer open.",
                speech="That window seems to have gone away.",
            )

    def activate(self, title: str) -> None:
        self._act(
            title,
            'set frontmost of proc to true\n        perform action "AXRaise" of w',
        )

    def minimize(self, title: str) -> None:
        self._act(title, 'set value of attribute "AXMinimized" of w to true')

    def maximize(self, title: str) -> None:
        self._act(title, 'set value of attribute "AXZoomed" of w to true')

    def close(self, title: str) -> None:
        self._act(
            title,
            'perform action "AXPress" of (first button of w whose subrole is "AXCloseButton")',
        )


def _no_backend_message() -> str:
    """Actionable install hint for the current OS when no backend exists."""
    if is_linux():
        return (
            "No window-control backend is available. Install wmctrl "
            "(e.g. 'sudo apt install wmctrl') or xdotool, and make sure a graphical "
            "X11 session is running."
        )
    if is_windows():
        return "No window-control backend is available. Install it with 'pip install pygetwindow'."
    if is_macos():
        return (
            "No window-control backend is available: 'osascript' was not found on PATH "
            "(it normally ships with macOS)."
        )
    return "Window control is not supported on this platform."


def select_backend() -> WindowBackend:
    """Pick the best available window-control backend for this machine.

    Order: pygetwindow on Windows, the Linux binaries wmctrl / xdotool, then
    macOS AppleScript. pygetwindow is only consulted on Windows — its macOS
    port is broken/read-only, so macOS must always get the AppleScript
    backend even when the package is importable. Tests monkeypatch this
    function to inject a fake backend.

    Raises:
        ToolError: with an install hint when no backend is available.
    """
    if is_windows():
        gw = try_import("pygetwindow")
        if gw is not None and hasattr(gw, "getAllTitles"):
            return PyGetWindowBackend(gw)

    if is_linux():
        if shutil.which("wmctrl"):
            return WmctrlBackend()
        if shutil.which("xdotool"):
            return XdotoolBackend()

    if is_macos() and shutil.which("osascript"):
        return AppleScriptBackend()

    raise ToolError(_no_backend_message(), speech="I can't manage windows on this machine yet.")


# =============================================================================
# Tools
# =============================================================================

_TITLE_SCHEMA = ToolParameterSchema(
    properties={
        "title": {
            "type": "string",
            "description": (
                "Full or partial window title; matched case-insensitively against the "
                "titles of open windows and the best match is used."
            ),
        },
    },
    required=["title"],
)


class ListWindowsTool(BaseTool):
    """List the titles of every open window on the desktop."""

    name = "list_windows"
    description = "Lists the titles of all open windows on the desktop."
    permission_level = PermissionLevel.READ
    category = ToolCategory.DESKTOP
    aliases = ("windows", "open_windows", "show_windows", "window_list")
    mutating = False
    examples = (
        ToolExample(utterance="what windows are open", arguments={}),
        ToolExample(utterance="list my open windows", arguments={}),
    )
    input_schema = ToolParameterSchema(properties={}, required=[])

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        backend = select_backend()
        titles = await self.to_thread(backend.list_titles)

        count = len(titles)
        if count == 0:
            speech = "I don't see any open windows."
        elif count == 1:
            speech = f"One window is open: {titles[0]}."
        else:
            speech = f"{count} windows are open."

        return {
            "windows": titles,
            "count": count,
            "backend": backend.name,
            "speech": speech,
            "display": "\n".join(titles) if titles else "No open windows.",
        }


class _SingleWindowActionTool(BaseTool):
    """Shared plumbing for tools that act on one window picked by title."""

    category = ToolCategory.DESKTOP
    mutating = True
    input_schema = _TITLE_SCHEMA

    #: Name of the :class:`WindowBackend` method to invoke.
    backend_method = ""
    #: Past-tense verb for speech, e.g. "Minimized".
    past_tense = "Handled"

    async def _resolve_window(self, backend: WindowBackend, title: str) -> str:
        """Best-match ``title`` against the currently open windows."""
        titles = await self.to_thread(backend.list_titles)
        if not titles:
            raise ToolError("No open windows were found.", speech="I don't see any open windows.")
        matched = best_match(titles, title)
        if matched is None:
            sample = "; ".join(titles[:8])
            raise ToolError(
                f"No open window matches '{title}'. Open windows: {sample}",
                speech=f"I couldn't find a window matching {title}.",
            )
        return matched

    async def _run(self, title: str = "", **kwargs: Any) -> Dict[str, Any]:
        if not title or not str(title).strip():
            raise ToolError(
                "A window title (or part of one) is required.",
                speech="Which window did you mean?",
            )
        backend = select_backend()
        matched = await self._resolve_window(backend, str(title))
        action = getattr(backend, self.backend_method)
        try:
            await self.to_thread(action, matched)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize backend surprises
            raise ToolError(
                f"Could not {self.backend_method} '{matched}': {exc}",
                speech="That window action didn't work.",
            ) from exc

        logger.info("%s window %r via %s", self.backend_method, matched, backend.name)
        return {
            "window": matched,
            "requested": title,
            "action": self.backend_method,
            "backend": backend.name,
            "speech": f"{self.past_tense} {matched}.",
        }


class FocusWindowTool(_SingleWindowActionTool):
    """Bring the best-matching window to the foreground."""

    name = "focus_window"
    description = "Brings the window whose title best matches the given text to the foreground."
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ("switch_to", "activate_window", "bring_to_front")
    backend_method = "activate"
    past_tense = "Switched to"
    examples = (
        ToolExample(utterance="switch to firefox", arguments={"title": "firefox"}),
        ToolExample(utterance="focus the terminal window", arguments={"title": "terminal"}),
    )


class MinimizeWindowTool(_SingleWindowActionTool):
    """Minimize (iconify) the best-matching window."""

    name = "minimize_window"
    description = "Minimizes the window whose title best matches the given text."
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ("iconify_window", "hide_window")
    backend_method = "minimize"
    past_tense = "Minimized"
    examples = (
        ToolExample(utterance="minimize spotify", arguments={"title": "spotify"}),
        ToolExample(utterance="hide the browser window", arguments={"title": "browser"}),
    )


class MaximizeWindowTool(_SingleWindowActionTool):
    """Maximize the best-matching window."""

    name = "maximize_window"
    description = "Maximizes the window whose title best matches the given text."
    permission_level = PermissionLevel.DESKTOP_ACTION
    aliases = ("fullscreen_window", "zoom_window")
    backend_method = "maximize"
    past_tense = "Maximized"
    examples = (
        ToolExample(utterance="maximize the code editor", arguments={"title": "visual studio code"}),
        ToolExample(utterance="make firefox full screen", arguments={"title": "firefox"}),
    )


class CloseWindowTool(_SingleWindowActionTool):
    """Close the best-matching window (may discard unsaved work)."""

    name = "close_window"
    description = "Closes the window whose title best matches the given text (asks first)."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    aliases = ("quit_window", "dismiss_window")
    backend_method = "close"
    past_tense = "Closed"
    examples = (
        ToolExample(utterance="close the settings window", arguments={"title": "settings"}),
        ToolExample(utterance="close firefox", arguments={"title": "firefox"}),
    )


def get_tools() -> list[BaseTool]:
    return [
        ListWindowsTool(),
        FocusWindowTool(),
        MinimizeWindowTool(),
        MaximizeWindowTool(),
        CloseWindowTool(),
    ]
