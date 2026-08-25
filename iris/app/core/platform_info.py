"""Platform detection and optional-dependency capability probing.

IRIS ships one codebase that runs on Windows, Linux and macOS with a large
surface of *optional* automation dependencies (pyautogui, pygetwindow, pycaw,
pystray, faster-whisper, python-pptx ...). Nothing in the assistant may crash
just because an optional package is absent: tools degrade to a clear
"capability unavailable" result instead.

This module centralizes that logic so every tool asks the same question the
same way.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


class OS:
    """Canonical OS identifiers."""

    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"
    UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def current_os() -> str:
    """Return the canonical identifier for the host operating system."""
    if sys.platform.startswith("win"):
        return OS.WINDOWS
    if sys.platform == "darwin":
        return OS.MACOS
    if sys.platform.startswith("linux"):
        return OS.LINUX
    return OS.UNKNOWN


def is_windows() -> bool:
    return current_os() == OS.WINDOWS


def is_linux() -> bool:
    return current_os() == OS.LINUX


def is_macos() -> bool:
    return current_os() == OS.MACOS


@lru_cache(maxsize=256)
def has_module(module_name: str) -> bool:
    """True when ``module_name`` is importable without importing it."""
    if module_name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def try_import(module_name: str) -> Any | None:
    """Import and return a module, or ``None`` when it is unavailable."""
    if not has_module(module_name):
        return None
    try:
        return importlib.import_module(module_name)
    except Exception:  # pragma: no cover - defensive: broken optional install
        return None


@lru_cache(maxsize=64)
def has_binary(name: str) -> bool:
    """True when an executable named ``name`` is on PATH."""
    return shutil.which(name) is not None


@lru_cache(maxsize=1)
def has_display() -> bool:
    """True when a graphical session appears to be available.

    GUI automation (mouse, keyboard, screenshots, windows) is meaningless on a
    headless server, so tools consult this before attempting anything visual.
    """
    if is_windows() or is_macos():
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@dataclass
class Capability:
    """A single automation capability and why it is or is not available."""

    name: str
    available: bool
    detail: str = ""
    install_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "install_hint": self.install_hint,
        }


# name -> (python modules that satisfy it, binaries that satisfy it, pip hint)
_CAPABILITY_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "gui_input": (("pyautogui",), (), "pip install pyautogui"),
    "window_control": (("pygetwindow",), ("wmctrl", "xdotool"), "pip install pygetwindow"),
    "screenshot": (("mss", "PIL"), ("scrot", "gnome-screenshot", "spectacle"), "pip install mss pillow"),
    "ocr": (("pytesseract",), ("tesseract",), "pip install pytesseract  (plus the tesseract binary)"),
    "clipboard": (("pyperclip",), ("xclip", "xsel", "wl-copy"), "pip install pyperclip"),
    "audio_capture": (("sounddevice", "pyaudio"), ("arecord",), "pip install sounddevice"),
    "audio_playback": (("sounddevice", "simpleaudio"), ("aplay", "paplay", "ffplay"), "pip install sounddevice"),
    "stt_local": (("faster_whisper", "vosk", "whisper"), (), "pip install faster-whisper"),
    "stt_online": (("speech_recognition",), (), "pip install SpeechRecognition"),
    "tts_local": (("pyttsx3", "piper"), ("espeak-ng", "espeak", "say", "piper"), "pip install pyttsx3"),
    "tts_online": (("edge_tts", "gtts"), (), "pip install edge-tts"),
    "wakeword": (("openwakeword", "pvporcupine"), (), "pip install openwakeword"),
    "vad": (("webrtcvad", "silero_vad"), (), "pip install webrtcvad"),
    "tray": (("pystray",), (), "pip install pystray pillow"),
    "webview": (("webview",), (), "pip install pywebview"),
    "hotkeys": (("pynput", "keyboard"), (), "pip install pynput"),
    "notifications": (("plyer", "win10toast"), ("notify-send", "osascript"), "pip install plyer"),
    "pptx": (("pptx",), (), "pip install python-pptx"),
    "docx": (("docx",), (), "pip install python-docx"),
    "xlsx": (("openpyxl",), (), "pip install openpyxl"),
    "pdf": (("reportlab", "pypdf"), (), "pip install reportlab pypdf"),
    "charts": (("matplotlib",), (), "pip install matplotlib"),
    "qrcode": (("qrcode",), (), "pip install 'qrcode[pil]'"),
    "scheduler": (("apscheduler",), (), "pip install apscheduler"),
    "volume_windows": (("pycaw", "comtypes"), (), "pip install pycaw comtypes"),
    "volume_linux": ((), ("amixer", "pactl", "wpctl"), "install alsa-utils or pulseaudio-utils"),
    "html_extract": (("bs4",), (), "pip install beautifulsoup4"),
    "fuzzy": (("rapidfuzz",), (), "pip install rapidfuzz"),
}


def capability(name: str) -> Capability:
    """Resolve a single named capability."""
    spec = _CAPABILITY_SPECS.get(name)
    if spec is None:
        return Capability(name=name, available=False, detail="Unknown capability name.")

    modules, binaries, hint = spec
    found_modules = [m for m in modules if has_module(m)]
    found_binaries = [b for b in binaries if has_binary(b)]

    if found_modules or found_binaries:
        parts = []
        if found_modules:
            parts.append("modules: " + ", ".join(found_modules))
        if found_binaries:
            parts.append("binaries: " + ", ".join(found_binaries))
        return Capability(name=name, available=True, detail="; ".join(parts), install_hint=hint)

    missing = " or ".join(modules + binaries) or "no known provider"
    return Capability(
        name=name,
        available=False,
        detail=f"Requires one of: {missing}.",
        install_hint=hint,
    )


def has_capability(name: str) -> bool:
    """Convenience boolean form of :func:`capability`."""
    return capability(name).available


def require_capability(name: str) -> Capability:
    """Return the capability, raising :class:`CapabilityError` when unavailable."""
    cap = capability(name)
    if not cap.available:
        raise CapabilityError(cap)
    return cap


class CapabilityError(RuntimeError):
    """Raised when a tool needs an optional dependency that is not installed."""

    def __init__(self, cap: Capability):
        self.capability = cap
        message = f"Capability '{cap.name}' is unavailable. {cap.detail}"
        if cap.install_hint:
            message += f" Install with: {cap.install_hint}"
        super().__init__(message)


@dataclass
class PlatformReport:
    """Full snapshot of the host platform and available capabilities."""

    os_name: str
    os_release: str
    os_version: str
    architecture: str
    python_version: str
    hostname: str
    has_display: bool
    capabilities: dict[str, Capability] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "os": self.os_name,
            "os_release": self.os_release,
            "os_version": self.os_version,
            "architecture": self.architecture,
            "python_version": self.python_version,
            "hostname": self.hostname,
            "has_display": self.has_display,
            "capabilities": {k: v.to_dict() for k, v in self.capabilities.items()},
            "available": sorted(k for k, v in self.capabilities.items() if v.available),
            "missing": sorted(k for k, v in self.capabilities.items() if not v.available),
        }


def platform_report() -> PlatformReport:
    """Build a full capability report (used by ``/api/v1/system/capabilities``)."""
    return PlatformReport(
        os_name=current_os(),
        os_release=platform.release(),
        os_version=platform.version(),
        architecture=platform.machine(),
        python_version=platform.python_version(),
        hostname=platform.node(),
        has_display=has_display(),
        capabilities={name: capability(name) for name in sorted(_CAPABILITY_SPECS)},
    )


def capability_names() -> list[str]:
    """All capability identifiers IRIS knows how to probe."""
    return sorted(_CAPABILITY_SPECS)


def reset_cache() -> None:
    """Clear cached probes (used by tests that fake module availability)."""
    for fn in (current_os, has_module, has_binary, has_display):
        fn.cache_clear()
