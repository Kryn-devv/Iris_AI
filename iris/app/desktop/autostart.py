"""Start-with-OS registration for IRIS on Windows, Linux and macOS.

Each platform gets its native mechanism:

* **Windows** — ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
  registry value (no admin rights needed).
* **Linux** — an XDG autostart ``.desktop`` entry in ``~/.config/autostart``.
* **macOS** — a per-user LaunchAgent plist in ``~/Library/LaunchAgents``.

The registered command is ``<current python> -m iris --minimized`` so the same
interpreter and environment that installed IRIS launches it at login.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from iris.app.core import paths
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import current_os, is_linux, is_macos, is_windows

logger = get_logger("desktop.autostart")

APP_ID = "IrisAssistant"
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command(minimized: bool = True) -> str:
    """The command line used to launch IRIS at login."""
    python = sys.executable or "python3"
    suffix = " --minimized" if minimized else ""
    if is_windows():
        # pythonw avoids a console window when available.
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)
        return f'"{python}" -m iris{suffix}'
    return f'"{python}" -m iris{suffix}'


# ---------------------------------------------------------------- Windows
def _win_enable() -> None:
    import winreg  # type: ignore[import-not-found]

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, launch_command())


def _win_disable() -> None:
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_ID)
    except FileNotFoundError:
        pass


def _win_enabled() -> bool:
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_ID)
            return True
    except FileNotFoundError:
        return False


# ------------------------------------------------------------------ Linux
def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / "iris-assistant.desktop"


def _linux_enable() -> None:
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=IRIS Assistant\n"
        "Comment=Personal desktop AI assistant\n"
        f"Exec={launch_command()}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Terminal=false\n"
    )
    path = _linux_desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry, encoding="utf-8")


def _linux_disable() -> None:
    _linux_desktop_path().unlink(missing_ok=True)


def _linux_enabled() -> bool:
    return _linux_desktop_path().exists()


# ------------------------------------------------------------------ macOS
def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.iris.assistant.plist"


def _mac_enable() -> None:
    python = sys.executable or "python3"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.iris.assistant</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>iris</string>
        <string>--minimized</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>{paths.logs_dir() / "launchd.log"}</string>
    <key>StandardErrorPath</key><string>{paths.logs_dir() / "launchd.err"}</string>
</dict>
</plist>
"""
    path = _mac_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True, check=False)


def _mac_disable() -> None:
    path = _mac_plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True, check=False)
        path.unlink(missing_ok=True)


def _mac_enabled() -> bool:
    return _mac_plist_path().exists()


# -------------------------------------------------------------------- API
def enable() -> dict[str, Any]:
    """Register IRIS to start at login."""
    if is_windows():
        _win_enable()
    elif is_linux():
        _linux_enable()
    elif is_macos():
        _mac_enable()
    else:
        raise RuntimeError(f"Autostart is not supported on '{current_os()}'.")
    logger.info("Autostart enabled (%s).", current_os())
    return status()


def disable() -> dict[str, Any]:
    """Remove the start-at-login registration."""
    if is_windows():
        _win_disable()
    elif is_linux():
        _linux_disable()
    elif is_macos():
        _mac_disable()
    logger.info("Autostart disabled (%s).", current_os())
    return status()


def is_enabled() -> bool:
    try:
        if is_windows():
            return _win_enabled()
        if is_linux():
            return _linux_enabled()
        if is_macos():
            return _mac_enabled()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Autostart check failed: %s", exc)
    return False


def status() -> dict[str, Any]:
    return {
        "enabled": is_enabled(),
        "os": current_os(),
        "command": launch_command(),
    }
