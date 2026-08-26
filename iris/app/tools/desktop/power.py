"""Power management tools: lock, sleep, shutdown, restart, cancel.

These are the highest-consequence desktop actions IRIS can take, so two
independent guards apply:

1. **Permission levels** — sleeping needs confirmation
   (``CONFIRM_REQUIRED``); shutting down and restarting are
   ``HIGH_RISK_ACTION`` and therefore refused entirely unless
   ``ALLOW_HIGH_RISK_ACTIONS`` is on *and* the user confirms.
2. **A dedicated kill switch** — every tool in this module except
   :class:`LockScreenTool` re-checks ``settings.ALLOW_POWER_ACTIONS`` at
   the top of ``_run`` and refuses when it is off (the default). Locking
   the screen is exempt because it is always safe.

Shutdown and restart are always *scheduled*, never immediate: the delay is
floored at :data:`MIN_SHUTDOWN_DELAY_SECONDS` seconds so
:class:`CancelShutdownTool` can still abort them.

Platform commands used (always list argv, never a shell):

===========  ==============================================================
Action       Windows / Linux / macOS
===========  ==============================================================
lock         ``rundll32 user32.dll,LockWorkStation`` /
             ``loginctl lock-session`` → ``xdg-screensaver lock`` →
             ``gnome-screensaver-command -l`` / Ctrl+Cmd+Q via ``osascript``
             (needs Accessibility) → ``open -a ScreenSaverEngine`` →
             ``pmset displaysleepnow``
sleep        ``rundll32 powrprof.dll,SetSuspendState 0,1,0`` /
             ``systemctl suspend`` / ``pmset sleepnow``
shutdown     ``shutdown /s /t N`` / ``shutdown -h +M`` /
             detached ``osascript`` (delay N, then System Events shut down)
restart      ``shutdown /r /t N`` / ``shutdown -r +M`` /
             detached ``osascript`` (delay N, then System Events restart)
cancel       ``shutdown /a`` / ``shutdown -c`` / ``pkill -f`` of the
             pending osascript
===========  ==============================================================

All subprocess work funnels through the module-level :func:`_run_command`
and :func:`_spawn_detached` helpers (monkeypatchable in tests) and runs
off the event loop via ``BaseTool.to_thread``.
"""

from __future__ import annotations

import math
import subprocess
from typing import Any, Dict

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import OS, current_os, has_binary, is_windows
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.power")


#: Shortest allowed shutdown/restart delay, so the user can always cancel.
MIN_SHUTDOWN_DELAY_SECONDS = 5
#: Longest allowed shutdown/restart delay (one hour).
MAX_SHUTDOWN_DELAY_SECONDS = 3600
#: Default shutdown/restart delay.
DEFAULT_SHUTDOWN_DELAY_SECONDS = 15

#: Unique marker embedded (as an AppleScript ``--`` comment) in the delayed
#: macOS shutdown/restart commands; ``cancel_shutdown`` pkills processes
#: whose command line contains it. Kept dash-free so it can be passed to
#: ``pkill -f`` verbatim, and unique so nothing else is ever matched.
_MACOS_POWER_MARKER = "iris-power"


# =============================================================================
# Shared helpers
# =============================================================================


def _run_command(argv: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a short-lived command with list argv (never a shell).

    Kept at module level so tests can monkeypatch it and assert the exact
    argv without executing anything.
    """
    return subprocess.run(  # noqa: S603 - argv list, no shell
        argv, capture_output=True, text=True, timeout=timeout
    )


def _spawn_detached(argv: list[str]) -> None:
    """Launch a long-running command detached from IRIS.

    Used for the macOS delayed shutdown/restart osascript, which sleeps for
    the whole grace period and must outlive (and never block) this process.
    """
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if is_windows():  # pragma: no cover - windows-only branch
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)  # noqa: S603 - argv list, no shell


def _checked_run(argv: list[str], *, what: str) -> subprocess.CompletedProcess:
    """Run a command and raise a clean ToolError on a nonzero exit."""
    try:
        completed = _run_command(argv)
    except FileNotFoundError:
        raise ToolError(
            f"'{argv[0]}' is not installed, so I can't {what}.",
            speech=f"I couldn't {what} — a required program is missing.",
        ) from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(
            f"Running '{argv[0]}' failed while trying to {what}: {exc}",
            speech=f"I couldn't {what}.",
        ) from None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ToolError(
            f"'{' '.join(argv)}' failed (exit {completed.returncode})"
            + (f": {detail}" if detail else "."),
            speech=f"I couldn't {what}.",
        )
    return completed


def _ensure_power_allowed() -> None:
    """Refuse power actions unless the dedicated settings switch is on."""
    if not settings.ALLOW_POWER_ACTIONS:
        raise ToolError(
            "Power actions are disabled. Set ALLOW_POWER_ACTIONS=true in your .env to enable them.",
            speech="Power actions are disabled in my settings.",
        )


def _unsupported_os(what: str) -> ToolError:
    return ToolError(
        f"I can't {what} on '{current_os()}'.",
        speech=f"I can't {what} on this system.",
    )


def _coerce_delay(delay_seconds: Any) -> int:
    """Coerce and clamp the shutdown/restart delay to a cancellable window."""
    if delay_seconds is None:
        return DEFAULT_SHUTDOWN_DELAY_SECONDS
    if isinstance(delay_seconds, bool):
        raise ToolError(f"'delay_seconds' must be a whole number, got {delay_seconds!r}.")
    try:
        value = int(delay_seconds)
    except (TypeError, ValueError):
        raise ToolError(
            f"'delay_seconds' must be a whole number, got {delay_seconds!r}."
        ) from None
    return max(MIN_SHUTDOWN_DELAY_SECONDS, min(MAX_SHUTDOWN_DELAY_SECONDS, value))


def _schedule_halt(mode: str, delay: int) -> Dict[str, Any]:
    """Schedule a shutdown ('halt') or restart in ``delay`` seconds.

    Returns the command that was issued and the *actual* number of seconds
    until it fires — Linux ``shutdown`` only takes whole minutes, so the
    delay is rounded up there.
    """
    verb = "shut down" if mode == "halt" else "restart"
    os_name = current_os()

    if os_name == OS.WINDOWS:
        flag = "/s" if mode == "halt" else "/r"
        argv = ["shutdown", flag, "/t", str(delay)]
        _checked_run(argv, what=f"schedule the {verb}")
        return {"command": " ".join(argv), "scheduled_in_seconds": delay}

    if os_name == OS.LINUX:
        minutes = max(1, math.ceil(delay / 60))
        flag = "-h" if mode == "halt" else "-r"
        argv = ["shutdown", flag, f"+{minutes}"]
        _checked_run(argv, what=f"schedule the {verb}")
        return {"command": " ".join(argv), "scheduled_in_seconds": minutes * 60}

    if os_name == OS.MACOS:
        event = "shut down" if mode == "halt" else "restart"
        argv = [
            "osascript",
            "-e", f"delay {delay} -- {_MACOS_POWER_MARKER}",
            "-e", f'tell application "System Events" to {event}',
        ]
        _spawn_detached(argv)
        return {"command": " ".join(argv), "scheduled_in_seconds": delay}

    raise _unsupported_os(verb)


# =============================================================================
# Tools
# =============================================================================


class LockScreenTool(BaseTool):
    """Lock the screen. Always safe, so exempt from ALLOW_POWER_ACTIONS."""

    name = "lock_screen"
    description = "Locks the screen so a password is needed to get back in."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.SYSTEM
    aliases = ("lock", "lock_computer", "lock_pc")
    mutating = True
    examples = (
        ToolExample(utterance="lock my screen", arguments={}),
        ToolExample(utterance="lock the computer, I'm stepping away", arguments={}),
    )
    input_schema = ToolParameterSchema(properties={}, required=[])

    #: Linux screen lockers in preference order: (binary, argv).
    LINUX_LOCKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("loginctl", ("loginctl", "lock-session")),
        ("xdg-screensaver", ("xdg-screensaver", "lock")),
        ("gnome-screensaver-command", ("gnome-screensaver-command", "-l")),
    )

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        return await self.to_thread(self._lock)

    def _lock(self) -> Dict[str, Any]:
        os_name = current_os()

        if os_name == OS.WINDOWS:
            argv = ["rundll32", "user32.dll,LockWorkStation"]
            _checked_run(argv, what="lock the screen")
            return {"command": " ".join(argv), "speech": "Locked the screen."}

        if os_name == OS.MACOS:
            return self._lock_macos()

        if os_name == OS.LINUX:
            failures: list[str] = []
            for binary, candidate in self.LINUX_LOCKERS:
                if not has_binary(binary):
                    continue
                try:
                    _checked_run(list(candidate), what="lock the screen")
                except ToolError as exc:
                    failures.append(str(exc))
                    continue
                return {"command": " ".join(candidate), "speech": "Locked the screen."}
            if failures:
                raise ToolError(
                    "Every screen locker failed: " + " | ".join(failures),
                    speech="I couldn't lock the screen.",
                )
            raise ToolError(
                "No screen locker found. Install one of: loginctl (systemd), "
                "xdg-screensaver (xdg-utils) or gnome-screensaver.",
                speech="I couldn't find a screen locker on this system.",
            )

        raise _unsupported_os("lock the screen")

    #: AppleScript for the macOS lock shortcut (Ctrl+Cmd+Q). Needs the
    #: Accessibility permission for the process hosting IRIS.
    MACOS_LOCK_KEYSTROKE = (
        'tell application "System Events" to '
        'keystroke "q" using {control down, command down}'
    )

    @classmethod
    def _lock_macos(cls) -> Dict[str, Any]:
        """Lock a Mac: real lock keystroke, then screensaver, then display sleep."""
        # 1. The real lock (Ctrl+Cmd+Q). Fails without Accessibility permission.
        argv = ["osascript", "-e", cls.MACOS_LOCK_KEYSTROKE]
        try:
            _checked_run(argv, what="lock the screen")
            return {"command": " ".join(argv), "speech": "Locked the screen."}
        except ToolError:
            pass
        # 2. The screensaver — locks when "require password after screen saver
        #    begins" is enabled, and needs no special permission.
        argv = ["open", "-a", "ScreenSaverEngine"]
        try:
            _checked_run(argv, what="lock the screen")
            return {"command": " ".join(argv), "speech": "Locked the screen."}
        except ToolError:
            pass
        # 3. Last resort: sleep the display. Only locks if password-on-wake is on.
        argv = ["pmset", "displaysleepnow"]
        _checked_run(argv, what="lock the screen")
        return {
            "command": " ".join(argv),
            "speech": "I put the display to sleep. It only locks if your Mac "
                      "requires a password on wake, and a real lock needs "
                      "Accessibility permission for IRIS.",
        }


class SleepTool(BaseTool):
    """Put the computer to sleep (suspend to RAM)."""

    name = "sleep_pc"
    description = "Puts the computer to sleep (suspend); unsaved work stays in memory."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.SYSTEM
    aliases = ("suspend", "sleep_computer")
    mutating = True
    examples = (
        ToolExample(utterance="put the computer to sleep", arguments={}),
        ToolExample(utterance="suspend the pc", arguments={}),
    )
    input_schema = ToolParameterSchema(properties={}, required=[])

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        _ensure_power_allowed()
        return await self.to_thread(self._sleep)

    @staticmethod
    def _sleep() -> Dict[str, Any]:
        os_name = current_os()
        if os_name == OS.WINDOWS:
            argv = ["rundll32", "powrprof.dll,SetSuspendState", "0,1,0"]
        elif os_name == OS.LINUX:
            argv = ["systemctl", "suspend"]
        elif os_name == OS.MACOS:
            argv = ["pmset", "sleepnow"]
        else:
            raise _unsupported_os("put the computer to sleep")

        _checked_run(argv, what="put the computer to sleep")
        return {"command": " ".join(argv), "speech": "Putting the computer to sleep."}


class ShutdownTool(BaseTool):
    """Schedule a full shutdown with a cancellable grace period."""

    name = "shutdown_pc"
    description = "Shuts the computer down after a short cancellable delay (default 15 seconds)."
    permission_level = PermissionLevel.HIGH_RISK_ACTION
    category = ToolCategory.SYSTEM
    aliases = ("shutdown", "power_off")
    mutating = True
    examples = (
        ToolExample(utterance="shut down the computer", arguments={}),
        ToolExample(utterance="power off in one minute", arguments={"delay_seconds": 60}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "delay_seconds": {
                "type": "integer",
                "description": (
                    f"Seconds before the shutdown fires "
                    f"({MIN_SHUTDOWN_DELAY_SECONDS}-{MAX_SHUTDOWN_DELAY_SECONDS}, "
                    f"default {DEFAULT_SHUTDOWN_DELAY_SECONDS}; values outside are "
                    "clamped so 'cancel_shutdown' can still abort it)."
                ),
            },
        },
        required=[],
    )

    async def _run(self, delay_seconds: Any = None, **kwargs: Any) -> Dict[str, Any]:
        _ensure_power_allowed()
        delay = _coerce_delay(delay_seconds)
        outcome = await self.to_thread(_schedule_halt, "halt", delay)
        seconds = outcome["scheduled_in_seconds"]
        outcome.update(
            delay_seconds=seconds,
            cancel_hint="Use the 'cancel_shutdown' tool to abort.",
            speech=f"Shutting down in {seconds} seconds. Say cancel shutdown to stop it.",
        )
        return outcome


class RestartTool(BaseTool):
    """Schedule a restart with a cancellable grace period."""

    name = "restart_pc"
    description = "Restarts the computer after a short cancellable delay (default 15 seconds)."
    permission_level = PermissionLevel.HIGH_RISK_ACTION
    category = ToolCategory.SYSTEM
    aliases = ("reboot", "restart_computer")
    mutating = True
    examples = (
        ToolExample(utterance="restart the computer", arguments={}),
        ToolExample(utterance="reboot in two minutes", arguments={"delay_seconds": 120}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "delay_seconds": {
                "type": "integer",
                "description": (
                    f"Seconds before the restart fires "
                    f"({MIN_SHUTDOWN_DELAY_SECONDS}-{MAX_SHUTDOWN_DELAY_SECONDS}, "
                    f"default {DEFAULT_SHUTDOWN_DELAY_SECONDS}; values outside are "
                    "clamped so 'cancel_shutdown' can still abort it)."
                ),
            },
        },
        required=[],
    )

    async def _run(self, delay_seconds: Any = None, **kwargs: Any) -> Dict[str, Any]:
        _ensure_power_allowed()
        delay = _coerce_delay(delay_seconds)
        outcome = await self.to_thread(_schedule_halt, "restart", delay)
        seconds = outcome["scheduled_in_seconds"]
        outcome.update(
            delay_seconds=seconds,
            cancel_hint="Use the 'cancel_shutdown' tool to abort.",
            speech=f"Restarting in {seconds} seconds. Say cancel shutdown to stop it.",
        )
        return outcome


class CancelShutdownTool(BaseTool):
    """Abort a pending scheduled shutdown or restart."""

    name = "cancel_shutdown"
    description = "Cancels a pending scheduled shutdown or restart."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.SYSTEM
    aliases = ("abort_shutdown", "cancel_restart", "stop_shutdown")
    mutating = True
    examples = (
        ToolExample(utterance="cancel the shutdown", arguments={}),
        ToolExample(utterance="don't restart, abort it", arguments={}),
    )
    input_schema = ToolParameterSchema(properties={}, required=[])

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        _ensure_power_allowed()
        return await self.to_thread(self._cancel)

    @staticmethod
    def _cancel() -> Dict[str, Any]:
        os_name = current_os()
        if os_name == OS.WINDOWS:
            argv = ["shutdown", "/a"]
        elif os_name == OS.LINUX:
            argv = ["shutdown", "-c"]
        elif os_name == OS.MACOS:
            # Kill the detached osascript this module spawned for the delay.
            argv = ["pkill", "-f", _MACOS_POWER_MARKER]
        else:
            raise _unsupported_os("cancel a shutdown")

        try:
            completed = _run_command(argv)
        except FileNotFoundError:
            raise ToolError(
                f"'{argv[0]}' is not installed, so I can't cancel the shutdown.",
                speech="I couldn't cancel the shutdown.",
            ) from None
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolError(
                f"Cancelling the shutdown failed: {exc}",
                speech="I couldn't cancel the shutdown.",
            ) from None

        # A nonzero exit here almost always means "nothing was pending"
        # (Windows error 1116, pkill's exit 1) — that's a no-op, not a failure.
        if completed.returncode != 0:
            return {
                "command": " ".join(argv),
                "cancelled": False,
                "speech": "There was no pending shutdown to cancel.",
            }
        return {
            "command": " ".join(argv),
            "cancelled": True,
            "speech": "Cancelled the pending shutdown.",
        }


def get_tools() -> list[BaseTool]:
    return [
        LockScreenTool(),
        SleepTool(),
        ShutdownTool(),
        RestartTool(),
        CancelShutdownTool(),
    ]
