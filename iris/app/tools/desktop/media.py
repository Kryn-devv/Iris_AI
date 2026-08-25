"""System volume and media-playback control tools.

Two tools live here:

* :class:`VolumeTool` — get, set, nudge, mute and unmute the master output
  volume, with a backend per operating system:

  - **Windows** — the ``pycaw`` + ``comtypes`` CoreAudio bindings
    (capability ``volume_windows``).
  - **Linux** — whichever mixer binary exists, preferred in this order:
    ``wpctl`` (PipeWire/WirePlumber), ``pactl`` (PulseAudio), ``amixer``
    (ALSA) — capability ``volume_linux``.
  - **macOS** — ``osascript`` with ``set volume output volume N``.

* :class:`MediaControlTool` — play/pause, next, previous and stop, using
  ``playerctl`` on Linux when present, ``osascript`` media key codes on
  macOS, and pyautogui's media keys everywhere pyautogui is usable.

Neither tool declares hard ``required_capabilities`` because every backend
has runtime fallbacks; unusable machines get a clean :class:`ToolError`
with an install hint instead. All subprocess work funnels through the
module-level :func:`_run_command` helper (monkeypatchable in tests) and is
executed off the event loop via ``BaseTool.to_thread``.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, Optional, Tuple

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import OS, current_os, has_binary, try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.media")


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


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _as_int(value: Any, name: str) -> int:
    """Coerce a model-supplied argument to int with a friendly error."""
    if isinstance(value, bool):
        raise ToolError(f"'{name}' must be a whole number, got {value!r}.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' must be a whole number, got {value!r}.") from None


# =============================================================================
# Volume: action normalization
# =============================================================================

#: Canonical volume actions.
VOLUME_ACTIONS: tuple[str, ...] = ("set", "up", "down", "mute", "unmute", "get")

#: Spelling variants people (and models) actually produce for volume actions.
VOLUME_ACTION_ALIASES: Dict[str, str] = {
    # set
    "set": "set", "setvolume": "set", "to": "set", "level": "set", "at": "set",
    # up
    "up": "up", "increase": "up", "raise": "up", "louder": "up", "higher": "up",
    "volumeup": "up", "turnup": "up", "boost": "up",
    # down
    "down": "down", "decrease": "down", "lower": "down", "quieter": "down",
    "softer": "down", "reduce": "down", "volumedown": "down", "turndown": "down",
    # mute
    "mute": "mute", "silence": "mute", "silent": "mute", "muteon": "mute",
    "soundoff": "mute", "off": "mute",
    # unmute
    "unmute": "unmute", "unsilence": "unmute", "muteoff": "unmute",
    "soundon": "unmute", "on": "unmute",
    # get
    "get": "get", "status": "get", "current": "get", "show": "get",
    "check": "get", "read": "get", "query": "get", "what": "get",
}


def normalize_volume_action(action: Any, level: Any = None) -> str:
    """Resolve a raw ``action`` argument to one of :data:`VOLUME_ACTIONS`.

    ``None`` defaults to ``"set"`` when a ``level`` was supplied (so
    ``{"level": 40}`` alone means "set the volume to 40") and to ``"get"``
    otherwise. Lookups are case-insensitive and ignore spaces, underscores
    and hyphens, so "Volume Up", "volume_up" and "UP" all resolve to "up".
    """
    if action is None:
        return "set" if level is not None else "get"
    key = str(action).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    resolved = VOLUME_ACTION_ALIASES.get(key)
    if resolved is None:
        valid = ", ".join(VOLUME_ACTIONS)
        raise ToolError(
            f"Unknown volume action {action!r}. Use one of: {valid}.",
            speech="I don't know that volume action.",
        )
    return resolved


# =============================================================================
# Volume: per-OS backends (sync — run via to_thread)
# =============================================================================


def _windows_endpoint(pycaw_mod: Any, comtypes_mod: Any) -> Any:
    """Build the CoreAudio master-volume endpoint via pycaw.

    Isolated so tests can monkeypatch this single function and hand the
    Windows backend a fake endpoint without any COM/ctypes machinery.
    """
    from ctypes import POINTER, cast  # stdlib; local to keep the hot path lean

    device = pycaw_mod.AudioUtilities.GetSpeakers()
    interface = device.Activate(
        pycaw_mod.IAudioEndpointVolume._iid_, comtypes_mod.CLSCTX_ALL, None
    )
    return cast(interface, POINTER(pycaw_mod.IAudioEndpointVolume))


def _volume_windows(action: str, level: Optional[int], step: int) -> Dict[str, Any]:
    """Windows master volume via pycaw/comtypes (CoreAudio)."""
    pycaw_mod = try_import("pycaw.pycaw")
    comtypes_mod = try_import("comtypes")
    if pycaw_mod is None or comtypes_mod is None:
        raise ToolError(
            "Volume control on Windows needs pycaw. Install with: pip install pycaw comtypes",
            speech="I can't control the volume — pycaw isn't installed.",
        )
    endpoint = _windows_endpoint(pycaw_mod, comtypes_mod)

    current = int(round(endpoint.GetMasterVolumeLevelScalar() * 100))
    muted = bool(endpoint.GetMute())

    if action == "get":
        return {"backend": "pycaw", "level": current, "muted": muted}
    if action == "mute":
        endpoint.SetMute(1, None)
        return {"backend": "pycaw", "level": current, "muted": True}
    if action == "unmute":
        endpoint.SetMute(0, None)
        return {"backend": "pycaw", "level": current, "muted": False}

    if action == "set":
        target = int(level)  # validated + clamped by the tool
    elif action == "up":
        target = _clamp(current + step, 0, 100)
    else:  # down
        target = _clamp(current - step, 0, 100)

    endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
    return {"backend": "pycaw", "level": target, "muted": muted}


#: Linux mixer binaries in preference order (PipeWire, PulseAudio, ALSA).
LINUX_VOLUME_BACKENDS: tuple[str, ...] = ("wpctl", "pactl", "amixer")


def _pick_linux_volume_backend() -> str:
    for binary in LINUX_VOLUME_BACKENDS:
        if has_binary(binary):
            return binary
    raise ToolError(
        "No volume backend found. Install one of: wireplumber (wpctl), "
        "pulseaudio-utils (pactl) or alsa-utils (amixer).",
        speech="I couldn't find a volume mixer on this system.",
    )


def _linux_get(backend: str) -> Tuple[Optional[int], Optional[bool]]:
    """Read (level 0-100, muted) from the chosen Linux backend."""
    if backend == "wpctl":
        out = _checked_run(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], what="read the volume"
        ).stdout
        match = re.search(r"(\d+(?:\.\d+)?)", out)
        level = int(round(float(match.group(1)) * 100)) if match else None
        return level, ("[MUTED]" in out)

    if backend == "pactl":
        out = _checked_run(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"], what="read the volume"
        ).stdout
        match = re.search(r"(\d{1,3})%", out)
        level = int(match.group(1)) if match else None
        mute_out = _checked_run(
            ["pactl", "get-sink-mute", "@DEFAULT_SINK@"], what="read the mute state"
        ).stdout
        muted: Optional[bool] = None
        if "yes" in mute_out.lower():
            muted = True
        elif "no" in mute_out.lower():
            muted = False
        return level, muted

    # amixer
    out = _checked_run(["amixer", "sget", "Master"], what="read the volume").stdout
    match = re.search(r"\[(\d{1,3})%\]", out)
    level = int(match.group(1)) if match else None
    muted = None
    if "[off]" in out:
        muted = True
    elif "[on]" in out:
        muted = False
    return level, muted


def _linux_set(backend: str, level: int) -> None:
    if backend == "wpctl":
        argv = ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level / 100:.2f}"]
    elif backend == "pactl":
        argv = ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"]
    else:
        argv = ["amixer", "-q", "sset", "Master", f"{level}%"]
    _checked_run(argv, what="set the volume")


def _linux_set_mute(backend: str, muted: bool) -> None:
    if backend == "wpctl":
        argv = ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if muted else "0"]
    elif backend == "pactl":
        argv = ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0"]
    else:
        argv = ["amixer", "-q", "sset", "Master", "mute" if muted else "unmute"]
    _checked_run(argv, what="mute the audio" if muted else "unmute the audio")


def _volume_linux(action: str, level: Optional[int], step: int) -> Dict[str, Any]:
    """Linux master volume via wpctl, pactl or amixer (first one on PATH)."""
    backend = _pick_linux_volume_backend()

    if action == "get":
        got, muted = _linux_get(backend)
        return {"backend": backend, "level": got, "muted": muted}
    if action in ("mute", "unmute"):
        _linux_set_mute(backend, action == "mute")
        return {"backend": backend, "level": None, "muted": action == "mute"}

    if action == "set":
        target = int(level)
    else:
        current, _ = _linux_get(backend)
        if current is None:
            raise ToolError(
                f"Couldn't parse the current volume from {backend}, so I can't "
                f"step it {action}. Try 'set' with an absolute level instead.",
                speech="I couldn't read the current volume.",
            )
        delta = step if action == "up" else -step
        target = _clamp(current + delta, 0, 100)

    _linux_set(backend, target)
    return {"backend": backend, "level": target, "muted": None}


def _volume_macos(action: str, level: Optional[int], step: int) -> Dict[str, Any]:
    """macOS output volume via osascript ('set volume output volume N')."""

    def _get_level() -> Optional[int]:
        out = _checked_run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            what="read the volume",
        ).stdout.strip()
        try:
            return int(float(out))
        except ValueError:
            return None

    if action == "get":
        got = _get_level()
        out = _checked_run(
            ["osascript", "-e", "output muted of (get volume settings)"],
            what="read the mute state",
        ).stdout.strip().lower()
        muted = True if out == "true" else False if out == "false" else None
        return {"backend": "osascript", "level": got, "muted": muted}

    if action in ("mute", "unmute"):
        flag = "true" if action == "mute" else "false"
        _checked_run(
            ["osascript", "-e", f"set volume output muted {flag}"],
            what="mute the audio" if action == "mute" else "unmute the audio",
        )
        return {"backend": "osascript", "level": None, "muted": action == "mute"}

    if action == "set":
        target = int(level)
    else:
        current = _get_level()
        if current is None:
            raise ToolError(
                "Couldn't read the current volume from macOS, so I can't step it. "
                "Try 'set' with an absolute level instead.",
                speech="I couldn't read the current volume.",
            )
        delta = step if action == "up" else -step
        target = _clamp(current + delta, 0, 100)

    _checked_run(
        ["osascript", "-e", f"set volume output volume {target}"],
        what="set the volume",
    )
    return {"backend": "osascript", "level": target, "muted": None}


# =============================================================================
# VolumeTool
# =============================================================================


class VolumeTool(BaseTool):
    """Get, set, nudge, mute or unmute the master output volume."""

    name = "volume"
    description = "Gets, sets, raises, lowers, mutes or unmutes the system master volume."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.MEDIA
    aliases = ("set_volume", "volume_up", "volume_down", "mute")
    mutating = True
    examples = (
        ToolExample(utterance="set the volume to 50 percent", arguments={"action": "set", "level": 50}),
        ToolExample(utterance="turn the volume up", arguments={"action": "up"}),
        ToolExample(utterance="lower the volume a lot", arguments={"action": "down", "step": 25}),
        ToolExample(utterance="mute the sound", arguments={"action": "mute"}),
        ToolExample(utterance="unmute", arguments={"action": "unmute"}),
        ToolExample(utterance="how loud is the volume right now", arguments={"action": "get"}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "action": {
                "type": "string",
                "enum": list(VOLUME_ACTIONS),
                "description": (
                    "What to do: 'set' (needs 'level'), 'up', 'down', 'mute', "
                    "'unmute' or 'get'. Defaults to 'set' when 'level' is given, "
                    "else 'get'."
                ),
            },
            "level": {
                "type": "integer",
                "description": "Target volume 0-100 for action 'set' (values outside are clamped).",
            },
            "step": {
                "type": "integer",
                "description": "How many percentage points 'up'/'down' move (1-100, default 10).",
            },
        },
        required=["action"],
    )

    #: Default nudge size for 'up'/'down'.
    DEFAULT_STEP = 10

    async def _run(
        self,
        action: Any = None,
        level: Any = None,
        step: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        act = normalize_volume_action(action, level)

        target: Optional[int] = None
        if act == "set":
            if level is None:
                raise ToolError(
                    "Setting the volume needs a 'level' between 0 and 100.",
                    speech="What level should I set the volume to?",
                )
            target = _clamp(_as_int(level, "level"), 0, 100)

        nudge = self.DEFAULT_STEP if step is None else _clamp(_as_int(step, "step"), 1, 100)

        os_name = current_os()
        if os_name == OS.WINDOWS:
            backend_fn = _volume_windows
        elif os_name == OS.LINUX:
            backend_fn = _volume_linux
        elif os_name == OS.MACOS:
            backend_fn = _volume_macos
        else:
            raise ToolError(
                f"Volume control is not supported on '{os_name}'.",
                speech="I can't control the volume on this system.",
            )

        outcome = await self.to_thread(backend_fn, act, target, nudge)
        outcome.update(action=act, speech=self._speech(act, outcome))
        return outcome

    @staticmethod
    def _speech(action: str, outcome: Dict[str, Any]) -> str:
        level = outcome.get("level")
        if action == "get":
            if level is None:
                return "I couldn't read the current volume level."
            suffix = " and muted" if outcome.get("muted") else ""
            return f"The volume is at {level} percent{suffix}."
        if action == "set":
            return f"Volume set to {level} percent."
        if action == "up":
            return f"Turned the volume up to {level} percent." if level is not None else "Turned the volume up."
        if action == "down":
            return f"Turned the volume down to {level} percent." if level is not None else "Turned the volume down."
        if action == "mute":
            return "Muted the audio."
        return "Unmuted the audio."


# =============================================================================
# Media playback: action normalization
# =============================================================================

#: Canonical media actions.
MEDIA_ACTIONS: tuple[str, ...] = ("play_pause", "next", "previous", "stop")

#: Spelling variants for playback actions, matched after lowercasing and
#: stripping spaces/underscores/hyphens.
MEDIA_ACTION_ALIASES: Dict[str, str] = {
    # play/pause toggle
    "playpause": "play_pause", "play": "play_pause", "pause": "play_pause",
    "toggle": "play_pause", "resume": "play_pause", "playmusic": "play_pause",
    "pausemusic": "play_pause", "togglemusic": "play_pause",
    # next
    "next": "next", "nexttrack": "next", "nextsong": "next", "skip": "next",
    "skiptrack": "next", "skipsong": "next", "forward": "next",
    # previous
    "previous": "previous", "prev": "previous", "prevtrack": "previous",
    "previoustrack": "previous", "previoussong": "previous", "back": "previous",
    "lastsong": "previous", "rewind": "previous",
    # stop
    "stop": "stop", "stopmusic": "stop", "stopplayback": "stop", "halt": "stop",
}


def normalize_media_action(action: Any) -> str:
    """Resolve a raw playback ``action`` to one of :data:`MEDIA_ACTIONS`."""
    if action is None:
        raise ToolError(
            "Provide an 'action': play_pause, next, previous or stop.",
            speech="What should I do with the music?",
        )
    key = str(action).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    resolved = MEDIA_ACTION_ALIASES.get(key)
    if resolved is None:
        valid = ", ".join(MEDIA_ACTIONS)
        raise ToolError(
            f"Unknown media action {action!r}. Use one of: {valid}.",
            speech="I don't know that playback action.",
        )
    return resolved


#: playerctl subcommand per canonical action.
PLAYERCTL_COMMANDS: Dict[str, str] = {
    "play_pause": "play-pause",
    "next": "next",
    "previous": "previous",
    "stop": "stop",
}

#: pyautogui key name per canonical action.
PYAUTOGUI_MEDIA_KEYS: Dict[str, str] = {
    "play_pause": "playpause",
    "next": "nexttrack",
    "previous": "prevtrack",
    "stop": "stop",
}

#: macOS virtual key codes for the media keys (F7/F8/F9 row). macOS has no
#: discrete "stop" media key, so stop falls back to the play/pause toggle.
MACOS_MEDIA_KEY_CODES: Dict[str, int] = {
    "play_pause": 100,
    "next": 101,
    "previous": 98,
    "stop": 100,
}


def _media_linux(action: str) -> Dict[str, Any]:
    """Linux playback control: playerctl (MPRIS) first, pyautogui fallback."""
    if has_binary("playerctl"):
        _checked_run(["playerctl", PLAYERCTL_COMMANDS[action]], what="control playback")
        return {"backend": "playerctl"}

    pag = try_import("pyautogui")
    if pag is not None:
        pag.FAILSAFE = True
        pag.press(PYAUTOGUI_MEDIA_KEYS[action])
        return {"backend": "pyautogui"}

    raise ToolError(
        "Media control needs playerctl (e.g. 'sudo apt install playerctl') or "
        "pyautogui ('pip install pyautogui') with a graphical session.",
        speech="I can't control media playback on this machine.",
    )


def _media_macos(action: str) -> Dict[str, Any]:
    """macOS playback control by sending the media key's virtual key code."""
    code = MACOS_MEDIA_KEY_CODES[action]
    _checked_run(
        ["osascript", "-e", f'tell application "System Events" to key code {code}'],
        what="control playback",
    )
    return {"backend": "osascript", "key_code": code}


def _media_windows(action: str) -> Dict[str, Any]:
    """Windows playback control via pyautogui's media keys."""
    pag = try_import("pyautogui")
    if pag is None:
        raise ToolError(
            "Media control on Windows needs pyautogui. Install with: pip install pyautogui",
            speech="I can't control media playback — pyautogui isn't installed.",
        )
    pag.FAILSAFE = True
    pag.press(PYAUTOGUI_MEDIA_KEYS[action])
    return {"backend": "pyautogui"}


class MediaControlTool(BaseTool):
    """Control media playback: play/pause toggle, next, previous, stop."""

    name = "media_control"
    description = "Controls media playback: toggle play/pause, next or previous track, or stop."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.MEDIA
    aliases = (
        "play_pause", "next_track", "previous_track",
        "pause_music", "play_music", "stop_music",
    )
    mutating = True
    examples = (
        ToolExample(utterance="pause the music", arguments={"action": "play_pause"}),
        ToolExample(utterance="play the next song", arguments={"action": "next"}),
        ToolExample(utterance="go back to the previous track", arguments={"action": "previous"}),
        ToolExample(utterance="stop the music", arguments={"action": "stop"}),
        ToolExample(utterance="skip this song", arguments={"action": "skip"}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "action": {
                "type": "string",
                "enum": list(MEDIA_ACTIONS),
                "description": "Playback action: 'play_pause', 'next', 'previous' or 'stop'.",
            },
        },
        required=["action"],
    )

    _SPEECH: Dict[str, str] = {
        "play_pause": "Toggled play pause.",
        "next": "Skipped to the next track.",
        "previous": "Went back to the previous track.",
        "stop": "Stopped playback.",
    }

    async def _run(self, action: Any = None, **kwargs: Any) -> Dict[str, Any]:
        act = normalize_media_action(action)

        os_name = current_os()
        if os_name == OS.LINUX:
            backend_fn = _media_linux
        elif os_name == OS.MACOS:
            backend_fn = _media_macos
        elif os_name == OS.WINDOWS:
            backend_fn = _media_windows
        else:
            raise ToolError(
                f"Media control is not supported on '{os_name}'.",
                speech="I can't control media playback on this system.",
            )

        outcome = await self.to_thread(backend_fn, act)
        outcome.update(action=act, speech=self._SPEECH[act])
        return outcome


def get_tools() -> list[BaseTool]:
    return [VolumeTool(), MediaControlTool()]
