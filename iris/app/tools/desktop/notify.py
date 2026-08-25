"""Desktop notification (toast) tool with layered cross-platform fallbacks.

The tool declares no ``required_capabilities`` — notifications have many
viable providers, so :func:`send_notification` walks a fallback chain at
call time:

1. ``plyer`` when importable (it picks the best native backend itself).
2. ``notify-send`` on Linux (libnotify; ships with most desktops).
3. ``osascript -e 'display notification ...'`` on macOS (built in).
4. PowerShell on Windows: ``New-BurntToastNotification`` when the BurntToast
   module is installed, otherwise the built-in ``msg.exe`` as a last resort.
5. A clean :class:`ToolError` carrying an install hint when nothing works.

Text is never interpolated into a shell string: subprocess argv lists are
used throughout, AppleScript literals are escaped with
:func:`_escape_applescript` and PowerShell literals with
:func:`_escape_powershell`. The chain lives in module functions so tests can
monkeypatch ``try_import``, ``shutil.which`` and ``subprocess.run``.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, List, Optional

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import is_linux, is_macos, is_windows, try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.notify")

#: Per-binary timeout; a wedged notification daemon must not hang the tool.
_SUBPROCESS_TIMEOUT = 10.0

#: Toast titles longer than this are truncated (platform daemons clip anyway).
MAX_TITLE_CHARS = 120
#: Toast bodies longer than this are truncated.
MAX_MESSAGE_CHARS = 2000

#: Seconds a toast stays visible where the backend supports a timeout.
_TOAST_SECONDS = 10


# =============================================================================
# Escaping helpers (pure logic, unit-testable)
# =============================================================================


def _escape_applescript(text: str) -> str:
    """Escape a string for embedding inside AppleScript double quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _escape_powershell(text: str) -> str:
    """Escape a string for embedding inside PowerShell single quotes."""
    return text.replace("'", "''")


def _install_hint() -> str:
    """Actionable hint for the current OS when no notification provider works."""
    if is_linux():
        return (
            "Install a notification provider: 'pip install plyer', or the libnotify "
            "tools ('sudo apt install libnotify-bin' for notify-send). "
            "A desktop session with a notification daemon must be running."
        )
    if is_macos():
        return "Install plyer with 'pip install plyer' (osascript should normally exist)."
    if is_windows():
        return (
            "Install plyer with 'pip install plyer', or the BurntToast PowerShell module "
            "('Install-Module BurntToast')."
        )
    return "Install plyer with 'pip install plyer'."


# =============================================================================
# Fallback chain (module-level so tests can monkeypatch its collaborators)
# =============================================================================


def _powershell_binary() -> Optional[str]:
    """Path to Windows PowerShell (or pwsh) when available."""
    return shutil.which("powershell") or shutil.which("pwsh")


def _burnttoast_available(powershell: str) -> bool:
    """True when the BurntToast PowerShell module is installed."""
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-Command", "Get-Module -ListAvailable -Name BurntToast"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "BurntToast" in (proc.stdout or "")


def _try_subprocess(name: str, argv: List[str], errors: List[str]) -> bool:
    """Run one backend command; record a failure detail and return success."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"{name}: {exc}")
        return False
    if proc.returncode == 0:
        return True
    detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
    errors.append(f"{name}: {detail}")
    return False


def send_notification(title: str, message: str) -> str:
    """Show a desktop notification, returning the name of the backend used.

    Raises:
        ToolError: when no provider is installed or all of them fail.
    """
    errors: List[str] = []

    plyer = try_import("plyer")
    if plyer is not None:
        try:
            plyer.notification.notify(
                title=title, message=message, app_name="IRIS", timeout=_TOAST_SECONDS
            )
            return "plyer"
        except Exception as exc:  # noqa: BLE001 - fall through to platform tools
            errors.append(f"plyer: {exc}")
            logger.debug("plyer notification failed (%s); trying platform tools.", exc)

    if is_linux() and shutil.which("notify-send"):
        argv = ["notify-send", "--app-name=IRIS", "--", title, message]
        if _try_subprocess("notify-send", argv, errors):
            return "notify-send"

    if is_macos() and shutil.which("osascript"):
        script = (
            f'display notification "{_escape_applescript(message)}" '
            f'with title "{_escape_applescript(title)}"'
        )
        if _try_subprocess("osascript", ["osascript", "-e", script], errors):
            return "osascript"

    if is_windows():
        powershell = _powershell_binary()
        if powershell and _burnttoast_available(powershell):
            command = (
                "New-BurntToastNotification -Text "
                f"'{_escape_powershell(title)}', '{_escape_powershell(message)}'"
            )
            argv = [powershell, "-NoProfile", "-Command", command]
            if _try_subprocess("BurntToast", argv, errors):
                return "burnttoast"
        if shutil.which("msg"):
            argv = ["msg", "*", f"/TIME:{_TOAST_SECONDS}", f"{title}: {message}"]
            if _try_subprocess("msg.exe", argv, errors):
                return "msg.exe"

    detail = f" Attempted: {'; '.join(errors)}." if errors else ""
    raise ToolError(
        f"Could not show a desktop notification. {_install_hint()}{detail}",
        speech="I couldn't show a notification on this machine.",
    )


# =============================================================================
# Tool
# =============================================================================


class NotifyTool(BaseTool):
    """Show a desktop notification (toast) with a title and message."""

    name = "notify"
    description = "Shows a desktop notification (toast) with a title and a message."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("notification", "alert", "toast")
    mutating = True
    examples = (
        ToolExample(
            utterance="notify me that the build finished",
            arguments={"title": "Build", "message": "The build finished."},
        ),
        ToolExample(
            utterance="pop up a reminder to stretch",
            arguments={"title": "Reminder", "message": "Time to stretch!"},
        ),
        ToolExample(
            utterance="alert me: download complete",
            arguments={"message": "Download complete."},
        ),
    )
    input_schema = ToolParameterSchema(
        properties={
            "title": {
                "type": "string",
                "description": "Short notification headline (defaults to 'IRIS').",
            },
            "message": {
                "type": "string",
                "description": "Body text of the notification.",
            },
        },
        required=["message"],
    )

    async def _run(self, message: str = "", title: str = "IRIS", **kwargs: Any) -> Dict[str, Any]:
        body = str(message or "").strip()
        if not body:
            raise ToolError(
                "A notification message is required.",
                speech="What should the notification say?",
            )
        headline = str(title or "").strip() or "IRIS"

        headline = headline[:MAX_TITLE_CHARS]
        body = body[:MAX_MESSAGE_CHARS]

        backend = await self.to_thread(send_notification, headline, body)
        logger.info("Notification shown via %s: %r", backend, headline)
        return {
            "title": headline,
            "message": body,
            "backend": backend,
            "speech": "Notification sent.",
            "display": f"Notification shown via {backend}: {headline} — {body}",
        }


def get_tools() -> list[BaseTool]:
    return [NotifyTool()]
