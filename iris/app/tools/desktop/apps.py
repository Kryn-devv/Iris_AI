"""Cross-platform application launching, closing and discovery tools.

This module gives IRIS a spoken-language friendly way to manage desktop
applications on Windows, Linux and macOS:

* :class:`OpenAppTool`  — "open notepad", "launch chrome", "start spotify"
* :class:`CloseAppTool` — "close spotify", "quit vlc" (confirmation required)
* :class:`ListAppsTool` — "what apps do you know?" / installed-app inventory

The heart of the module is :data:`APP_SPECS`, a rich alias table that maps the
names people actually say ("vs code", "file explorer", "task manager") to
per-operating-system launch specifications. The pure resolver
:func:`resolve_app` is exported so the deterministic NLU layer and the tests
can resolve names without launching anything.

Launch strategy per OS:

* **Windows** — ``os.startfile(target)`` first (handles executables on PATH
  and ``ms-settings:`` style URIs), then known absolute install paths, then
  ``cmd /c start "" target`` as a last resort.
* **Linux**   — ``shutil.which`` over a list of candidate binaries, then a
  detached ``subprocess.Popen``.
* **macOS**   — ``open -a "App Name"`` for each candidate application name.
"""

from __future__ import annotations

import difflib
import glob as _glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import current_os, try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.apps")

__all__ = [
    "AppSpec",
    "APP_SPECS",
    "APP_ALIASES",
    "resolve_app",
    "suggest_apps",
    "OpenAppTool",
    "CloseAppTool",
    "ListAppsTool",
    "get_tools",
]


# =============================================================================
# Alias table
# =============================================================================


@dataclass(frozen=True)
class AppSpec:
    """Per-OS launch specification for one well-known application."""

    key: str
    label: str
    group: str
    #: Windows launch targets: executables resolvable by ``start``/``os.startfile``
    #: or shell URIs such as ``ms-settings:``.
    windows: tuple[str, ...] = ()
    #: Known absolute Windows install locations (support ``%VAR%`` and ``*`` globs).
    windows_paths: tuple[str, ...] = ()
    #: Linux candidate commands; the first word is looked up with ``shutil.which``.
    linux: tuple[str, ...] = ()
    #: macOS application names for ``open -a``.
    macos: tuple[str, ...] = ()
    #: Process base names (case-insensitive, ``.exe`` stripped) used by close_app.
    processes: tuple[str, ...] = ()


_CHROME_WIN_PATHS = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
)
_FIREFOX_WIN_PATHS = (
    r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
    r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
)
_EDGE_WIN_PATHS = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
)


#: Canonical application table. Keys are stable identifiers used across IRIS.
APP_SPECS: dict[str, AppSpec] = {
    spec.key: spec
    for spec in (
        AppSpec(
            key="notepad",
            label="Notepad",
            group="editors",
            windows=("notepad",),
            linux=("gedit", "kate", "mousepad", "xed", "pluma", "leafpad", "featherpad"),
            macos=("TextEdit",),
            processes=("notepad", "gedit", "kate", "mousepad", "xed", "pluma", "leafpad",
                       "featherpad", "textedit"),
        ),
        AppSpec(
            key="wordpad",
            label="WordPad",
            group="editors",
            windows=("write", "wordpad"),
            linux=("abiword", "libreoffice --writer"),
            macos=("TextEdit",),
            processes=("wordpad", "write", "abiword"),
        ),
        AppSpec(
            key="calculator",
            label="Calculator",
            group="utilities",
            windows=("calc",),
            linux=("gnome-calculator", "kcalc", "galculator", "mate-calc", "qalculate-gtk",
                   "xcalc"),
            macos=("Calculator",),
            processes=("calc", "calculator", "calculatorapp", "gnome-calculator", "kcalc",
                       "galculator", "mate-calc", "xcalc"),
        ),
        AppSpec(
            key="paint",
            label="Paint",
            group="graphics",
            windows=("mspaint",),
            linux=("kolourpaint", "pinta", "drawing", "krita", "gimp"),
            macos=("Preview",),
            processes=("mspaint", "paintapp", "kolourpaint", "pinta", "drawing", "krita",
                       "gimp", "preview"),
        ),
        AppSpec(
            key="browser",
            label="Web Browser",
            group="browsers",
            windows=("chrome", "msedge", "firefox"),
            windows_paths=_CHROME_WIN_PATHS + _EDGE_WIN_PATHS + _FIREFOX_WIN_PATHS,
            linux=("x-www-browser", "sensible-browser", "google-chrome",
                   "google-chrome-stable", "chromium", "chromium-browser", "firefox",
                   "brave-browser", "microsoft-edge"),
            macos=("Safari",),
            processes=("chrome", "msedge", "firefox", "chromium", "brave", "safari"),
        ),
        AppSpec(
            key="chrome",
            label="Google Chrome",
            group="browsers",
            windows=("chrome",),
            windows_paths=_CHROME_WIN_PATHS,
            linux=("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"),
            macos=("Google Chrome",),
            processes=("chrome", "google-chrome", "google chrome", "chromium",
                       "chromium-browser"),
        ),
        AppSpec(
            key="firefox",
            label="Mozilla Firefox",
            group="browsers",
            windows=("firefox",),
            windows_paths=_FIREFOX_WIN_PATHS,
            linux=("firefox", "firefox-esr"),
            macos=("Firefox",),
            processes=("firefox", "firefox-esr", "firefox-bin"),
        ),
        AppSpec(
            key="edge",
            label="Microsoft Edge",
            group="browsers",
            windows=("msedge",),
            windows_paths=_EDGE_WIN_PATHS,
            linux=("microsoft-edge", "microsoft-edge-stable"),
            macos=("Microsoft Edge",),
            processes=("msedge", "microsoft-edge", "microsoft edge"),
        ),
        AppSpec(
            key="terminal",
            label="Terminal",
            group="system",
            windows=("wt", "cmd"),
            linux=("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
                   "tilix", "alacritty", "kitty", "xterm"),
            macos=("Terminal",),
            processes=("windowsterminal", "cmd", "gnome-terminal", "gnome-terminal-server",
                       "konsole", "xfce4-terminal", "tilix", "alacritty", "kitty", "xterm",
                       "terminal"),
        ),
        AppSpec(
            key="powershell",
            label="PowerShell",
            group="system",
            windows=("powershell", "pwsh"),
            linux=("pwsh",),
            macos=("PowerShell",),
            processes=("powershell", "powershell_ise", "pwsh"),
        ),
        AppSpec(
            key="file_manager",
            label="File Manager",
            group="system",
            windows=("explorer",),
            linux=("nautilus", "dolphin", "thunar", "pcmanfm", "nemo", "caja"),
            macos=("Finder",),
            processes=("explorer", "nautilus", "dolphin", "thunar", "pcmanfm", "nemo",
                       "caja", "finder"),
        ),
        AppSpec(
            key="vscode",
            label="Visual Studio Code",
            group="editors",
            windows=("code",),
            windows_paths=(r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe",),
            linux=("code", "code-insiders", "codium", "code-oss"),
            macos=("Visual Studio Code",),
            processes=("code", "code-insiders", "codium", "code-oss"),
        ),
        AppSpec(
            key="word",
            label="Microsoft Word",
            group="office",
            windows=("winword",),
            windows_paths=(r"%ProgramFiles%\Microsoft Office\root\Office16\WINWORD.EXE",),
            linux=("lowriter", "libreoffice --writer"),
            macos=("Microsoft Word",),
            processes=("winword", "lowriter", "soffice", "soffice.bin", "microsoft word"),
        ),
        AppSpec(
            key="excel",
            label="Microsoft Excel",
            group="office",
            windows=("excel",),
            windows_paths=(r"%ProgramFiles%\Microsoft Office\root\Office16\EXCEL.EXE",),
            linux=("localc", "libreoffice --calc"),
            macos=("Microsoft Excel",),
            processes=("excel", "localc", "soffice", "soffice.bin", "microsoft excel"),
        ),
        AppSpec(
            key="powerpoint",
            label="Microsoft PowerPoint",
            group="office",
            windows=("powerpnt",),
            windows_paths=(r"%ProgramFiles%\Microsoft Office\root\Office16\POWERPNT.EXE",),
            linux=("loimpress", "libreoffice --impress"),
            macos=("Microsoft PowerPoint",),
            processes=("powerpnt", "loimpress", "soffice", "soffice.bin",
                       "microsoft powerpoint"),
        ),
        AppSpec(
            key="task_manager",
            label="Task Manager",
            group="system",
            windows=("taskmgr",),
            linux=("gnome-system-monitor", "plasma-systemmonitor", "ksysguard",
                   "mate-system-monitor", "xfce4-taskmanager"),
            macos=("Activity Monitor",),
            processes=("taskmgr", "gnome-system-monitor", "plasma-systemmonitor",
                       "ksysguard", "activity monitor"),
        ),
        AppSpec(
            key="settings",
            label="Settings",
            group="system",
            windows=("ms-settings:", "control"),
            linux=("gnome-control-center", "systemsettings", "systemsettings5",
                   "unity-control-center", "mate-control-center", "xfce4-settings-manager"),
            macos=("System Settings", "System Preferences"),
            processes=("systemsettings", "systemsettings5", "gnome-control-center",
                       "control"),
        ),
        AppSpec(
            key="spotify",
            label="Spotify",
            group="media",
            windows=("spotify",),
            windows_paths=(r"%AppData%\Spotify\Spotify.exe",),
            linux=("spotify", "spotify-launcher"),
            macos=("Spotify",),
            processes=("spotify",),
        ),
        AppSpec(
            key="vlc",
            label="VLC Media Player",
            group="media",
            windows=("vlc",),
            windows_paths=(r"%ProgramFiles%\VideoLAN\VLC\vlc.exe",
                           r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"),
            linux=("vlc",),
            macos=("VLC",),
            processes=("vlc",),
        ),
        AppSpec(
            key="discord",
            label="Discord",
            group="communication",
            windows=("discord",),
            windows_paths=(r"%LocalAppData%\Discord\app-*\Discord.exe",),
            linux=("discord", "discord-canary"),
            macos=("Discord",),
            processes=("discord", "discord-canary"),
        ),
        AppSpec(
            key="steam",
            label="Steam",
            group="gaming",
            windows=("steam",),
            windows_paths=(r"%ProgramFiles(x86)%\Steam\steam.exe",),
            linux=("steam",),
            macos=("Steam",),
            processes=("steam", "steamwebhelper"),
        ),
        AppSpec(
            key="camera",
            label="Camera",
            group="media",
            windows=("microsoft.windows.camera:",),
            linux=("cheese", "kamoso", "guvcview"),
            macos=("Photo Booth",),
            processes=("windowscamera", "cheese", "kamoso", "guvcview", "photo booth"),
        ),
        AppSpec(
            key="snipping_tool",
            label="Snipping Tool",
            group="utilities",
            windows=("ms-screenclip:", "snippingtool"),
            linux=("flameshot", "gnome-screenshot", "spectacle", "ksnip"),
            macos=("Screenshot",),
            processes=("snippingtool", "screenclippinghost", "flameshot",
                       "gnome-screenshot", "spectacle", "ksnip"),
        ),
    )
}


#: Spoken/typed name -> canonical APP_SPECS key. Keys are pre-normalized.
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "notepad": ("notepad", "note pad", "text editor", "editor", "gedit", "textedit"),
    "wordpad": ("wordpad", "word pad"),
    "calculator": ("calculator", "calc", "gnome calculator", "kcalc"),
    "paint": ("paint", "mspaint", "ms paint", "microsoft paint"),
    "browser": ("browser", "web browser", "default browser", "internet"),
    "chrome": ("chrome", "google chrome", "chromium"),
    "firefox": ("firefox", "mozilla", "mozilla firefox"),
    "edge": ("edge", "msedge", "microsoft edge"),
    "terminal": ("terminal", "cmd", "command prompt", "command line", "console", "shell",
                 "windows terminal"),
    "powershell": ("powershell", "power shell", "pwsh", "windows powershell"),
    "file_manager": ("file manager", "files", "explorer", "file explorer",
                     "windows explorer", "nautilus", "finder", "my computer", "this pc"),
    "vscode": ("vscode", "vs code", "code", "visual studio code", "visual studio"),
    "word": ("word", "ms word", "microsoft word", "winword"),
    "excel": ("excel", "ms excel", "microsoft excel", "spreadsheet"),
    "powerpoint": ("powerpoint", "power point", "ms powerpoint", "microsoft powerpoint",
                   "powerpnt"),
    "task_manager": ("task manager", "taskmgr", "system monitor", "process manager",
                     "activity monitor"),
    "settings": ("settings", "control panel", "system settings", "preferences",
                 "system preferences"),
    "spotify": ("spotify",),
    "vlc": ("vlc", "vlc media player", "media player", "video player"),
    "discord": ("discord",),
    "steam": ("steam",),
    "camera": ("camera", "webcam", "photo booth", "cheese"),
    "snipping_tool": ("snipping tool", "snip", "snip and sketch", "screen clip",
                      "screenshot tool"),
}


def _normalize_app_name(name: str) -> str:
    """Normalize a spoken/typed app name for alias lookup."""
    text = (name or "").strip().lower()
    text = text.replace("_", " ").replace("-", " ")
    for suffix in (".exe", ".app"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return re.sub(r"\s+", " ", text).strip()


def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for key, aliases in _RAW_ALIASES.items():
        index[_normalize_app_name(key)] = key
        index[_normalize_app_name(APP_SPECS[key].label)] = key
        for alias in aliases:
            index[_normalize_app_name(alias)] = key
    return index


#: Normalized alias -> canonical key, exported for the NLU layer.
APP_ALIASES: dict[str, str] = _build_alias_index()


def resolve_app(app: str) -> AppSpec | None:
    """Resolve a spoken/typed application name to its :class:`AppSpec`.

    Pure function with no side effects — safe for the NLU layer and tests.
    Returns ``None`` when the name matches nothing in the alias table.

    >>> resolve_app("VS Code").key
    'vscode'
    >>> resolve_app("no-such-thing") is None
    True
    """
    key = APP_ALIASES.get(_normalize_app_name(app))
    return APP_SPECS[key] if key else None


def suggest_apps(app: str, limit: int = 5) -> list[str]:
    """Closest alias-table matches for an unknown app name (for error hints)."""
    normalized = _normalize_app_name(app)
    if not normalized:
        return []
    matches = difflib.get_close_matches(normalized, list(APP_ALIASES), n=limit, cutoff=0.6)
    # De-duplicate by canonical key while preserving order.
    seen: set[str] = set()
    suggestions: list[str] = []
    for match in matches:
        key = APP_ALIASES[match]
        if key not in seen:
            seen.add(key)
            suggestions.append(match)
    return suggestions


# =============================================================================
# Launch helpers
# =============================================================================


def _popen_detached(argv: list[str]) -> Any:
    """Start a process fully detached from IRIS so it outlives the assistant."""
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def _expand_windows_paths(raw: str) -> list[str]:
    """Expand ``%VAR%`` variables and ``*`` globs in a Windows install path."""
    expanded = os.path.expandvars(raw)
    if "%" in expanded:  # an env var did not resolve on this machine
        return []
    if "*" in expanded:
        return sorted(_glob.glob(expanded), reverse=True)
    return [expanded]


def _launch_windows(spec: AppSpec) -> dict[str, str]:
    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        for target in spec.windows:
            try:
                startfile(target)
                return {"strategy": "os.startfile", "target": target}
            except OSError:
                continue
    for raw in spec.windows_paths:
        for path in _expand_windows_paths(raw):
            if os.path.isfile(path):
                _popen_detached([path])
                return {"strategy": "absolute_path", "target": path}
    for target in spec.windows:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", target])
            return {"strategy": "cmd_start", "target": target}
        except OSError:
            continue
    raise ToolError(
        f"Couldn't launch {spec.label} on Windows — none of its known targets worked.",
        speech=f"I couldn't find a way to open {spec.label}.",
    )


def _launch_linux(spec: AppSpec) -> dict[str, str]:
    for candidate in spec.linux:
        argv = candidate.split()
        binary = shutil.which(argv[0])
        if binary:
            _popen_detached([binary, *argv[1:]])
            return {"strategy": "which+popen", "target": binary}
    tried = ", ".join(candidate.split()[0] for candidate in spec.linux) or "(none known)"
    raise ToolError(
        f"{spec.label} doesn't appear to be installed — none of these binaries were "
        f"found on PATH: {tried}.",
        speech=f"{spec.label} doesn't seem to be installed.",
    )


def _launch_macos(spec: AppSpec) -> dict[str, str]:
    for app_name in spec.macos:
        proc = subprocess.run(["open", "-a", app_name], capture_output=True)
        if proc.returncode == 0:
            return {"strategy": "open -a", "target": app_name}
    raise ToolError(
        f"Couldn't launch {spec.label} on macOS — 'open -a' found no matching app.",
        speech=f"I couldn't find {spec.label} on this Mac.",
    )


def _launch_spec(spec: AppSpec) -> dict[str, str]:
    """Launch an app spec on the current OS and report which strategy worked."""
    osname = current_os()
    candidates = {
        "windows": spec.windows or spec.windows_paths,
        "linux": spec.linux,
        "macos": spec.macos,
    }.get(osname)
    if not candidates:
        raise ToolError(
            f"I don't know how to open {spec.label} on {osname}.",
            speech=f"I don't know how to open {spec.label} on this system.",
        )
    if osname == "windows":
        return _launch_windows(spec)
    if osname == "macos":
        return _launch_macos(spec)
    return _launch_linux(spec)


# =============================================================================
# Close helpers
# =============================================================================

#: Processes IRIS refuses to touch under any circumstance — killing these can
#: take down the OS session or the whole machine.
CRITICAL_PROCESSES: frozenset[str] = frozenset(
    {
        "systemd", "init", "kthreadd", "launchd", "kernel", "kernel_task",
        "winlogon", "csrss", "wininit", "smss", "services", "lsass", "svchost",
        "dwm", "system", "registry", "idle", "system idle process",
        "memory compression", "loginwindow", "windowserver", "fontdrvhost",
        "sshd", "dbus-daemon",
    }
)

#: Shell processes that may only be closed with an explicit ``force=true``
#: (killing them restarts the desktop shell). Gated per-OS so that e.g.
#: "close file manager" on Linux is not blocked by the Windows shell name.
_FORCE_GATED: dict[str, frozenset[str]] = {
    "windows": frozenset({"explorer"}),
    "macos": frozenset({"finder"}),
}


def _normalize_proc_name(name: str) -> str:
    text = (name or "").strip().lower()
    return text[:-4] if text.endswith(".exe") else text


def _terminate_processes(psutil_mod: Any, names: set[str]) -> dict[str, int]:
    """Terminate every process whose base name is in ``names`` (sync, threaded)."""
    access_denied_exc = getattr(psutil_mod, "AccessDenied", PermissionError)
    no_such_exc = getattr(psutil_mod, "NoSuchProcess", ProcessLookupError)
    own_pid = os.getpid()

    matched = []
    for proc in psutil_mod.process_iter(["pid", "name"]):
        try:
            pid = proc.info.get("pid")
            pname = _normalize_proc_name(proc.info.get("name") or "")
        except Exception:  # noqa: BLE001 - a process may vanish mid-iteration
            continue
        if pid == own_pid or pname in CRITICAL_PROCESSES:
            continue
        if pname in names:
            matched.append(proc)

    denied = 0
    for proc in matched:
        try:
            proc.terminate()
        except no_such_exc:
            pass
        except access_denied_exc:
            denied += 1

    if matched:
        gone, alive = psutil_mod.wait_procs(matched, timeout=3.0)
    else:
        gone, alive = [], []

    killed = 0
    for proc in alive:
        try:
            proc.kill()
            killed += 1
        except Exception:  # noqa: BLE001 - best effort escalation
            pass

    return {
        "matched": len(matched),
        "closed": len(gone) + killed,
        "forced_kill": killed,
        "access_denied": denied,
    }


# =============================================================================
# Tools
# =============================================================================


class OpenAppTool(BaseTool):
    """Launch a desktop application by its everyday spoken name.

    The alias table covers the apps people ask for most ("chrome", "vs code",
    "task manager", "control panel", ...). Unknown names fall back to a PATH
    lookup, and a miss produces did-you-mean suggestions from the table.
    """

    name = "open_app"
    description = "Opens a desktop application by name (e.g. Chrome, Notepad, VS Code, Spotify)."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("launch_app", "start_app", "run_app")
    mutating = True
    examples = (
        ToolExample(utterance="open chrome", arguments={"app": "chrome"}),
        ToolExample(utterance="launch vs code", arguments={"app": "vs code"}),
        ToolExample(utterance="start the calculator", arguments={"app": "calculator"}),
        ToolExample(utterance="open task manager", arguments={"app": "task manager"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "app": {
                "type": "string",
                "description": "Application name as spoken, e.g. 'chrome', 'notepad', "
                               "'vs code', 'file manager'.",
            }
        },
        required=["app"],
    )

    async def _run(self, app: str = "", **kwargs: Any) -> dict[str, Any]:
        requested = (app or "").strip()
        if not requested:
            raise ToolError("The 'app' argument is required.", speech="Which app should I open?")

        spec = resolve_app(requested)
        if spec is not None:
            launched = await self.to_thread(_launch_spec, spec)
            logger.info("Opened %s via %s (%s)", spec.key, launched["strategy"], launched["target"])
            return {
                "app": spec.key,
                "label": spec.label,
                "strategy": launched["strategy"],
                "target": launched["target"],
                "speech": f"Opened {spec.label}.",
                "display": f"Opened {spec.label} using {launched['strategy']} "
                           f"({launched['target']}).",
            }

        # Unknown alias — maybe it is a real binary on PATH ("htop", "blender").
        binary = await self.to_thread(shutil.which, requested)
        if binary:
            await self.to_thread(_popen_detached, [binary])
            logger.info("Opened unlisted app %r from PATH: %s", requested, binary)
            return {
                "app": requested,
                "label": requested,
                "strategy": "which+popen",
                "target": binary,
                "speech": f"Opened {requested}.",
                "display": f"Opened {requested} from {binary}.",
            }

        # On macOS 'open -a' matches installed .app bundles case-insensitively
        # ("open -a slack" launches Slack.app), so try it before giving up.
        if current_os() == "macos":
            proc = await self.to_thread(
                subprocess.run, ["open", "-a", requested], capture_output=True
            )
            if proc.returncode == 0:
                logger.info("Opened unlisted app %r via 'open -a'.", requested)
                return {
                    "app": requested,
                    "label": requested,
                    "strategy": "open -a",
                    "target": requested,
                    "speech": f"Opened {requested}.",
                    "display": f"Opened {requested} using open -a.",
                }

        suggestions = suggest_apps(requested)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        raise ToolError(
            f"I don't know how to open '{requested}' and it isn't on PATH.{hint}",
            speech=f"I couldn't find an app called {requested}."
            + (f" Did you mean {suggestions[0]}?" if suggestions else ""),
        )


class CloseAppTool(BaseTool):
    """Close a running application by name using graceful termination.

    Matching processes get ``terminate()`` followed by a three second grace
    period, then ``kill()`` for stragglers. Critical system processes are
    always refused; desktop shells (Explorer, Finder) need ``force=true``.
    """

    name = "close_app"
    description = "Closes a running application by name (graceful terminate, then kill)."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.DESKTOP
    aliases = ("quit_app", "kill_app")
    mutating = True
    examples = (
        ToolExample(utterance="close spotify", arguments={"app": "spotify"}),
        ToolExample(utterance="quit vlc", arguments={"app": "vlc"}),
        ToolExample(utterance="kill chrome", arguments={"app": "chrome"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "app": {
                "type": "string",
                "description": "Application name to close, e.g. 'spotify', 'chrome'.",
            },
            "force": {
                "type": "boolean",
                "description": "Required (true) to close desktop shells like Explorer/Finder.",
            },
        },
        required=["app"],
    )

    async def _run(self, app: str = "", force: bool = False, **kwargs: Any) -> dict[str, Any]:
        requested = (app or "").strip()
        if not requested:
            raise ToolError("The 'app' argument is required.", speech="Which app should I close?")

        psutil_mod = try_import("psutil")
        if psutil_mod is None:
            raise ToolError(
                "psutil is not installed, so I can't manage processes. "
                "Install with: pip install psutil",
                speech="I need the psutil package to close apps.",
            )

        spec = resolve_app(requested)
        if spec is not None:
            label = spec.label
            names = {_normalize_proc_name(p) for p in spec.processes}
        else:
            label = requested
            names = {_normalize_proc_name(requested)}
        names.discard("")

        critical = names & CRITICAL_PROCESSES
        if critical:
            raise ToolError(
                f"Refusing to close critical system process(es): {', '.join(sorted(critical))}. "
                f"Killing them can crash or lock the machine.",
                speech="I won't close critical system processes.",
            )

        gated = names & _FORCE_GATED.get(current_os(), frozenset())
        if gated and not force:
            raise ToolError(
                f"Closing {label} would terminate the desktop shell "
                f"({', '.join(sorted(gated))}). Repeat with force=true if you really "
                f"want that.",
                speech=f"Closing {label} would restart your desktop shell. "
                       f"Say the word and I'll force it.",
            )

        stats = await self.to_thread(_terminate_processes, psutil_mod, names)
        logger.info("close_app(%s): %s", requested, stats)

        if stats["matched"] == 0:
            raise ToolError(
                f"No running process matched {label}.",
                speech=f"{label} doesn't seem to be running.",
            )
        if stats["closed"] == 0:
            raise ToolError(
                f"Found {stats['matched']} {label} process(es) but couldn't close them "
                f"(access denied for {stats['access_denied']}).",
                speech=f"I found {label} running but wasn't allowed to close it.",
            )

        plural = "process" if stats["closed"] == 1 else "processes"
        return {
            "app": spec.key if spec else requested,
            "label": label,
            **stats,
            "speech": f"Closed {label}.",
            "display": f"Closed {stats['closed']} {label} {plural} "
                       f"({stats['forced_kill']} needed a hard kill).",
        }


class ListAppsTool(BaseTool):
    """Report which of the well-known apps are actually installed here.

    Probes the alias table against this machine (PATH lookups, known install
    paths, ``/Applications`` bundles) and groups the hits by app category.
    Read-only: nothing is launched.
    """

    name = "list_apps"
    description = "Lists which well-known applications are installed on this machine, grouped."
    permission_level = PermissionLevel.READ
    category = ToolCategory.DESKTOP
    aliases = ("installed_apps", "available_apps")
    mutating = False
    examples = (
        ToolExample(utterance="what apps can you open?", arguments={}),
        ToolExample(utterance="list installed apps", arguments={}),
    )
    input_schema = ToolParameterSchema(type="object", properties={}, required=[])

    @staticmethod
    def _find_installed(spec: AppSpec, osname: str) -> str | None:
        """Return the evidence (path/target) that an app is present, or None."""
        if osname == "windows":
            for target in spec.windows:
                if target.endswith(":"):
                    return target  # built-in shell URI (ms-settings:, camera, ...)
                found = shutil.which(target)
                if found:
                    return found
            for raw in spec.windows_paths:
                for path in _expand_windows_paths(raw):
                    if os.path.isfile(path):
                        return path
            return None
        if osname == "macos":
            for app_name in spec.macos:
                for root in ("/Applications", "/System/Applications",
                             "/System/Applications/Utilities",
                             "/System/Library/CoreServices",
                             str(Path.home() / "Applications")):
                    bundle = os.path.join(root, f"{app_name}.app")
                    if os.path.isdir(bundle):
                        return bundle
            return None
        for candidate in spec.linux:
            found = shutil.which(candidate.split()[0])
            if found:
                return found
        return None

    def _probe(self) -> dict[str, Any]:
        osname = current_os()
        groups: dict[str, list[dict[str, str]]] = {}
        missing: list[str] = []
        for spec in APP_SPECS.values():
            evidence = self._find_installed(spec, osname)
            if evidence:
                groups.setdefault(spec.group, []).append(
                    {"app": spec.key, "label": spec.label, "via": evidence}
                )
            else:
                missing.append(spec.key)
        ordered = {
            group: sorted(entries, key=lambda e: e["label"])
            for group, entries in sorted(groups.items())
        }
        return {"os": osname, "installed": ordered, "missing": sorted(missing)}

    async def _run(self, **kwargs: Any) -> dict[str, Any]:
        report = await self.to_thread(self._probe)
        count = sum(len(entries) for entries in report["installed"].values())
        lines = []
        for group, entries in report["installed"].items():
            names = ", ".join(entry["label"] for entry in entries)
            lines.append(f"{group}: {names}")
        return {
            **report,
            "installed_count": count,
            "speech": f"I can see {count} of the apps I know installed on this machine.",
            "display": "\n".join(lines) if lines else "No known applications were found.",
        }


def get_tools() -> list[BaseTool]:
    return [OpenAppTool(), CloseAppTool(), ListAppsTool()]
