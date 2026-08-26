"""Tests for the desktop media (volume / playback) and power tools.

The test environment is headless Linux with no mixer binaries, no
``playerctl``, no ``pyautogui`` and no ``pycaw``, so everything external is
monkeypatched:

* ``current_os`` is faked per test to exercise every OS branch,
* the module-level ``_run_command`` / ``_spawn_detached`` helpers are
  replaced with recorders so tests assert *exact argv lists* and nothing
  ever executes,
* ``has_binary`` / ``try_import`` are faked to steer backend selection,
* ``settings.ALLOW_POWER_ACTIONS`` is toggled to verify the kill switch.
"""

from __future__ import annotations

import subprocess

import pytest

from iris.app.core.security import PermissionLevel
from iris.app.tools.base import ToolError
from iris.app.tools.desktop import media as media_module
from iris.app.tools.desktop import power as power_module
from iris.app.tools.desktop.media import (
    MediaControlTool,
    VolumeTool,
    normalize_media_action,
    normalize_volume_action,
)
from iris.app.tools.desktop.power import (
    CancelShutdownTool,
    DEFAULT_SHUTDOWN_DELAY_SECONDS,
    LockScreenTool,
    MIN_SHUTDOWN_DELAY_SECONDS,
    RestartTool,
    ShutdownTool,
    SleepTool,
)

POWER_DISABLED_MESSAGE = (
    "Power actions are disabled. Set ALLOW_POWER_ACTIONS=true in your .env to enable them."
)


# =============================================================================
# Recorders and fakes
# =============================================================================


class CommandRecorder:
    """Fake for the modules' _run_command: records argv, returns scripted output."""

    def __init__(self, responses=None, default=(0, "", "")):
        self.calls: list[list[str]] = []
        self.responses = list(responses or [])
        self.default = default

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        rc, out, err = self.responses.pop(0) if self.responses else self.default
        return subprocess.CompletedProcess(list(argv), rc, stdout=out, stderr=err)


class FakeEndpoint:
    """Fake pycaw IAudioEndpointVolume pointer."""

    def __init__(self, level: float = 0.50, muted: bool = False):
        self.level = level
        self.muted = muted
        self.calls: list[tuple] = []

    def GetMasterVolumeLevelScalar(self):  # noqa: N802 - mirrors COM API
        return self.level

    def SetMasterVolumeLevelScalar(self, value, ctx):  # noqa: N802
        self.calls.append(("set_level", round(value, 4)))
        self.level = value

    def GetMute(self):  # noqa: N802
        return self.muted

    def SetMute(self, value, ctx):  # noqa: N802
        self.calls.append(("set_mute", value))
        self.muted = bool(value)


class FakePyAutoGUI:
    def __init__(self):
        self.FAILSAFE = False
        self.pressed: list[str] = []

    def press(self, key):
        self.pressed.append(key)


def fake_os(monkeypatch, module, name: str) -> None:
    monkeypatch.setattr(module, "current_os", lambda: name)


def only_binaries(monkeypatch, module, *names: str) -> None:
    monkeypatch.setattr(module, "has_binary", lambda name: name in names)


@pytest.fixture
def media_run(monkeypatch):
    recorder = CommandRecorder()
    monkeypatch.setattr(media_module, "_run_command", recorder)
    return recorder


@pytest.fixture
def power_run(monkeypatch):
    recorder = CommandRecorder()
    monkeypatch.setattr(power_module, "_run_command", recorder)
    return recorder


@pytest.fixture
def power_enabled(monkeypatch):
    monkeypatch.setattr(power_module.settings, "ALLOW_POWER_ACTIONS", True)


# =============================================================================
# Volume action normalization (pure logic)
# =============================================================================


def test_volume_action_aliases():
    assert normalize_volume_action("set") == "set"
    assert normalize_volume_action("Volume Up") == "up"
    assert normalize_volume_action("volume_up") == "up"
    assert normalize_volume_action("increase") == "up"
    assert normalize_volume_action("LOWER") == "down"
    assert normalize_volume_action("decrease") == "down"
    assert normalize_volume_action("silence") == "mute"
    assert normalize_volume_action("sound-on") == "unmute"
    assert normalize_volume_action("status") == "get"
    assert normalize_volume_action("current") == "get"


def test_volume_action_defaults():
    assert normalize_volume_action(None, level=40) == "set"
    assert normalize_volume_action(None, level=None) == "get"


def test_volume_action_unknown_rejected():
    with pytest.raises(ToolError) as excinfo:
        normalize_volume_action("explode")
    message = str(excinfo.value)
    assert "explode" in message
    assert "mute" in message and "get" in message  # valid options listed


# =============================================================================
# VolumeTool argument validation
# =============================================================================


async def test_volume_set_requires_level(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    with pytest.raises(ToolError) as excinfo:
        await VolumeTool()._run(action="set")
    assert "level" in str(excinfo.value)


async def test_volume_rejects_non_numeric_level(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    with pytest.raises(ToolError):
        await VolumeTool()._run(action="set", level="loud")
    with pytest.raises(ToolError):
        await VolumeTool()._run(action="set", level=True)
    with pytest.raises(ToolError):
        await VolumeTool()._run(action="up", step="big")


async def test_volume_unknown_os_rejected(monkeypatch):
    fake_os(monkeypatch, media_module, "unknown")
    result = await VolumeTool().execute(action="set", level=50)
    assert result.success is False
    assert "not supported" in result.error


# =============================================================================
# Volume on Linux: backend selection and exact argv
# =============================================================================


async def test_volume_set_prefers_wpctl(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "wpctl", "pactl", "amixer")
    result = await VolumeTool()._run(action="set", level=50)
    assert media_run.calls == [["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.50"]]
    assert result["backend"] == "wpctl"
    assert result["level"] == 50
    assert result["speech"] == "Volume set to 50 percent."


async def test_volume_set_falls_back_to_pactl(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "pactl", "amixer")
    result = await VolumeTool()._run(action="set", level=30)
    assert media_run.calls == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "30%"]]
    assert result["backend"] == "pactl"


async def test_volume_set_falls_back_to_amixer(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "amixer")
    result = await VolumeTool()._run(action="set", level=75)
    assert media_run.calls == [["amixer", "-q", "sset", "Master", "75%"]]
    assert result["backend"] == "amixer"


async def test_volume_no_linux_backend_helpful_error(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module)  # nothing on PATH
    result = await VolumeTool().execute(action="set", level=50)
    assert result.success is False
    for hint in ("wpctl", "pactl", "amixer"):
        assert hint in result.error
    assert media_run.calls == []


async def test_volume_level_clamped_high_and_low(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "wpctl")
    result = await VolumeTool()._run(action="set", level=250)
    assert media_run.calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.00"]
    assert result["level"] == 100

    result = await VolumeTool()._run(action="set", level=-40)
    assert media_run.calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.00"]
    assert result["level"] == 0


async def test_volume_up_reads_then_sets(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "wpctl")
    recorder = CommandRecorder(responses=[(0, "Volume: 0.50\n", ""), (0, "", "")])
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool()._run(action="up")  # default step 10
    assert recorder.calls == [
        ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.60"],
    ]
    assert result["level"] == 60
    assert "60 percent" in result["speech"]


async def test_volume_down_custom_step_clamps_at_zero(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "wpctl")
    recorder = CommandRecorder(responses=[(0, "Volume: 0.15\n", ""), (0, "", "")])
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool()._run(action="down", step=40)
    assert recorder.calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.00"]
    assert result["level"] == 0


async def test_volume_get_parses_wpctl_including_mute(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "wpctl")
    recorder = CommandRecorder(responses=[(0, "Volume: 0.62 [MUTED]\n", "")])
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool()._run(action="get")
    assert result["level"] == 62
    assert result["muted"] is True
    assert "62 percent and muted" in result["speech"]


async def test_volume_get_parses_amixer(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "amixer")
    out = "Simple mixer control 'Master',0\n  Mono: Playback 45 [62%] [-12.00dB] [on]\n"
    recorder = CommandRecorder(responses=[(0, out, "")])
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool()._run(action="get")
    assert recorder.calls == [["amixer", "sget", "Master"]]
    assert result["level"] == 62
    assert result["muted"] is False


async def test_volume_get_parses_pactl(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "pactl")
    recorder = CommandRecorder(
        responses=[
            (0, "Volume: front-left: 39321 /  60% / -13.31 dB\n", ""),
            (0, "Mute: yes\n", ""),
        ]
    )
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool()._run(action="get")
    assert recorder.calls == [
        ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
        ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
    ]
    assert result["level"] == 60
    assert result["muted"] is True


async def test_volume_mute_unmute_argv_per_backend(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")

    only_binaries(monkeypatch, media_module, "wpctl")
    result = await VolumeTool()._run(action="mute")
    assert media_run.calls[-1] == ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"]
    assert result["muted"] is True and result["speech"] == "Muted the audio."

    only_binaries(monkeypatch, media_module, "pactl")
    await VolumeTool()._run(action="unmute")
    assert media_run.calls[-1] == ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"]

    only_binaries(monkeypatch, media_module, "amixer")
    await VolumeTool()._run(action="mute")
    assert media_run.calls[-1] == ["amixer", "-q", "sset", "Master", "mute"]


async def test_volume_command_failure_becomes_tool_error(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "wpctl")
    recorder = CommandRecorder(default=(1, "", "sink not found"))
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool().execute(action="set", level=20)
    assert result.success is False
    assert "sink not found" in result.error


# =============================================================================
# Volume on macOS
# =============================================================================


async def test_volume_macos_set(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "macos")
    result = await VolumeTool()._run(action="set", level=30)
    assert media_run.calls == [["osascript", "-e", "set volume output volume 30"]]
    assert result["backend"] == "osascript"
    assert result["level"] == 30


async def test_volume_macos_get(monkeypatch):
    fake_os(monkeypatch, media_module, "macos")
    recorder = CommandRecorder(responses=[(0, "44\n", ""), (0, "false\n", "")])
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool()._run(action="get")
    assert recorder.calls == [
        ["osascript", "-e", "output volume of (get volume settings)"],
        ["osascript", "-e", "output muted of (get volume settings)"],
    ]
    assert result["level"] == 44
    assert result["muted"] is False


async def test_volume_macos_up_and_mute(monkeypatch):
    fake_os(monkeypatch, media_module, "macos")
    recorder = CommandRecorder(responses=[(0, "95\n", ""), (0, "", "")])
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await VolumeTool()._run(action="up", step=20)  # clamps 95+20 -> 100
    assert recorder.calls[-1] == ["osascript", "-e", "set volume output volume 100"]
    assert result["level"] == 100

    recorder.calls.clear()
    await VolumeTool()._run(action="mute")
    assert recorder.calls == [["osascript", "-e", "set volume output muted true"]]


# =============================================================================
# Volume on Windows (fake pycaw endpoint)
# =============================================================================


@pytest.fixture
def fake_windows_volume(monkeypatch):
    fake_os(monkeypatch, media_module, "windows")
    endpoint = FakeEndpoint(level=0.50, muted=False)
    monkeypatch.setattr(media_module, "try_import", lambda name: object())
    monkeypatch.setattr(media_module, "_windows_endpoint", lambda p, c: endpoint)
    return endpoint


async def test_volume_windows_set(fake_windows_volume):
    result = await VolumeTool()._run(action="set", level=80)
    assert ("set_level", 0.8) in fake_windows_volume.calls
    assert result["backend"] == "pycaw"
    assert result["level"] == 80


async def test_volume_windows_get(fake_windows_volume):
    result = await VolumeTool()._run(action="get")
    assert result["level"] == 50
    assert result["muted"] is False
    assert fake_windows_volume.calls == []  # read-only


async def test_volume_windows_up_clamps_at_100(fake_windows_volume):
    fake_windows_volume.level = 0.95
    result = await VolumeTool()._run(action="up", step=20)
    assert ("set_level", 1.0) in fake_windows_volume.calls
    assert result["level"] == 100


async def test_volume_windows_mute_and_unmute(fake_windows_volume):
    result = await VolumeTool()._run(action="mute")
    assert ("set_mute", 1) in fake_windows_volume.calls
    assert result["muted"] is True

    result = await VolumeTool()._run(action="unmute")
    assert ("set_mute", 0) in fake_windows_volume.calls
    assert result["muted"] is False


async def test_volume_windows_missing_pycaw(monkeypatch):
    fake_os(monkeypatch, media_module, "windows")
    monkeypatch.setattr(media_module, "try_import", lambda name: None)
    result = await VolumeTool().execute(action="set", level=50)
    assert result.success is False
    assert "pip install pycaw comtypes" in result.error


# =============================================================================
# Media control
# =============================================================================


def test_media_action_aliases():
    assert normalize_media_action("play_pause") == "play_pause"
    assert normalize_media_action("Play") == "play_pause"
    assert normalize_media_action("pause music") == "play_pause"
    assert normalize_media_action("skip") == "next"
    assert normalize_media_action("next track") == "next"
    assert normalize_media_action("prev") == "previous"
    assert normalize_media_action("previous-track") == "previous"
    assert normalize_media_action("STOP") == "stop"


def test_media_action_unknown_and_missing():
    with pytest.raises(ToolError):
        normalize_media_action("louder")
    with pytest.raises(ToolError):
        normalize_media_action(None)


async def test_media_linux_uses_playerctl(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "playerctl")
    result = await MediaControlTool()._run(action="play_pause")
    assert media_run.calls == [["playerctl", "play-pause"]]
    assert result["backend"] == "playerctl"
    assert result["speech"] == "Toggled play pause."

    await MediaControlTool()._run(action="next")
    assert media_run.calls[-1] == ["playerctl", "next"]
    await MediaControlTool()._run(action="previous")
    assert media_run.calls[-1] == ["playerctl", "previous"]
    await MediaControlTool()._run(action="stop")
    assert media_run.calls[-1] == ["playerctl", "stop"]


async def test_media_linux_playerctl_failure_reported(monkeypatch):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module, "playerctl")
    recorder = CommandRecorder(default=(1, "", "No players found"))
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await MediaControlTool().execute(action="next")
    assert result.success is False
    assert "No players found" in result.error


async def test_media_linux_falls_back_to_pyautogui(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module)  # no playerctl
    fake_pag = FakePyAutoGUI()
    monkeypatch.setattr(media_module, "try_import", lambda name: fake_pag)

    result = await MediaControlTool()._run(action="next")
    assert fake_pag.pressed == ["nexttrack"]
    assert fake_pag.FAILSAFE is True
    assert result["backend"] == "pyautogui"
    assert media_run.calls == []


async def test_media_linux_helpful_error_without_backends(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "linux")
    only_binaries(monkeypatch, media_module)
    monkeypatch.setattr(media_module, "try_import", lambda name: None)

    result = await MediaControlTool().execute(action="play_pause")
    assert result.success is False
    assert "playerctl" in result.error
    assert "pyautogui" in result.error


async def test_media_macos_targets_spotify_when_running(monkeypatch):
    fake_os(monkeypatch, media_module, "macos")
    recorder = CommandRecorder(responses=[(0, "true\n", "")])  # Spotify is running
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await MediaControlTool()._run(action="next")
    assert recorder.calls == [
        ["osascript", "-e", 'application "Spotify" is running'],
        ["osascript", "-e", 'tell application "Spotify" to next track'],
    ]
    assert result["backend"] == "osascript"
    assert result["player"] == "Spotify"


async def test_media_macos_falls_back_to_music_app(monkeypatch):
    fake_os(monkeypatch, media_module, "macos")
    recorder = CommandRecorder(responses=[(0, "false\n", "")])  # Spotify not running
    monkeypatch.setattr(media_module, "_run_command", recorder)

    result = await MediaControlTool()._run(action="play_pause")
    assert recorder.calls[-1] == ["osascript", "-e", 'tell application "Music" to playpause']
    assert result["player"] == "Music"


async def test_media_macos_player_verbs(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "macos")
    # Default recorder response is (0, "", "") -> Spotify probe says "not running".
    await MediaControlTool()._run(action="previous")
    assert media_run.calls[-1][-1] == 'tell application "Music" to previous track'
    await MediaControlTool()._run(action="stop")  # no discrete stop -> pause
    assert media_run.calls[-1][-1] == 'tell application "Music" to pause'


async def test_media_macos_probe_failure_defaults_to_music(monkeypatch, media_run):
    fake_os(monkeypatch, media_module, "macos")

    def _boom(argv, **kwargs):
        if "is running" in argv[-1]:
            raise OSError("osascript exploded")
        return media_run(argv, **kwargs)

    monkeypatch.setattr(media_module, "_run_command", _boom)
    result = await MediaControlTool()._run(action="next")
    assert result["player"] == "Music"
    assert media_run.calls == [["osascript", "-e", 'tell application "Music" to next track']]


async def test_media_windows_uses_pyautogui(monkeypatch):
    fake_os(monkeypatch, media_module, "windows")
    fake_pag = FakePyAutoGUI()
    monkeypatch.setattr(media_module, "try_import", lambda name: fake_pag)

    result = await MediaControlTool()._run(action="play_pause")
    assert fake_pag.pressed == ["playpause"]
    assert result["backend"] == "pyautogui"


async def test_media_windows_missing_pyautogui(monkeypatch):
    fake_os(monkeypatch, media_module, "windows")
    monkeypatch.setattr(media_module, "try_import", lambda name: None)
    result = await MediaControlTool().execute(action="stop")
    assert result.success is False
    assert "pip install pyautogui" in result.error


# =============================================================================
# Power: kill-switch gating (ALLOW_POWER_ACTIONS defaults to False)
# =============================================================================


@pytest.mark.parametrize(
    "tool_factory",
    [SleepTool, ShutdownTool, RestartTool, CancelShutdownTool],
)
async def test_power_tools_refused_by_default(tool_factory, power_run):
    assert power_module.settings.ALLOW_POWER_ACTIONS is False  # repo default
    result = await tool_factory().execute()
    assert result.success is False
    assert result.error == POWER_DISABLED_MESSAGE
    assert power_run.calls == []  # nothing was executed


async def test_lock_screen_exempt_from_power_flag(monkeypatch, power_run):
    assert power_module.settings.ALLOW_POWER_ACTIONS is False
    fake_os(monkeypatch, power_module, "linux")
    only_binaries(monkeypatch, power_module, "loginctl")

    result = await LockScreenTool().execute()
    assert result.success is True
    assert power_run.calls == [["loginctl", "lock-session"]]
    assert result.speech == "Locked the screen."


# =============================================================================
# Power: lock screen backends
# =============================================================================


async def test_lock_screen_windows(monkeypatch, power_run):
    fake_os(monkeypatch, power_module, "windows")
    result = await LockScreenTool()._run()
    assert power_run.calls == [["rundll32", "user32.dll,LockWorkStation"]]
    assert result["command"] == "rundll32 user32.dll,LockWorkStation"


_MAC_LOCK_KEYSTROKE = (
    'tell application "System Events" to keystroke "q" '
    "using {control down, command down}"
)


async def test_lock_screen_macos_keystroke_first(monkeypatch, power_run):
    fake_os(monkeypatch, power_module, "macos")
    result = await LockScreenTool()._run()
    assert power_run.calls == [["osascript", "-e", _MAC_LOCK_KEYSTROKE]]
    assert result["speech"] == "Locked the screen."


async def test_lock_screen_macos_falls_back_to_screensaver(monkeypatch):
    fake_os(monkeypatch, power_module, "macos")
    recorder = CommandRecorder(responses=[(1, "", "not allowed assistive access")])
    monkeypatch.setattr(power_module, "_run_command", recorder)

    result = await LockScreenTool()._run()
    assert recorder.calls == [
        ["osascript", "-e", _MAC_LOCK_KEYSTROKE],
        ["open", "-a", "ScreenSaverEngine"],
    ]
    assert result["command"] == "open -a ScreenSaverEngine"
    assert result["speech"] == "Locked the screen."


async def test_lock_screen_macos_last_resort_pmset(monkeypatch):
    fake_os(monkeypatch, power_module, "macos")
    recorder = CommandRecorder(responses=[(1, "", "no accessibility"), (1, "", "no engine")])
    monkeypatch.setattr(power_module, "_run_command", recorder)

    result = await LockScreenTool()._run()
    assert recorder.calls[-1] == ["pmset", "displaysleepnow"]
    assert result["command"] == "pmset displaysleepnow"
    # The speech warns that this only locks with password-on-wake enabled.
    assert "password" in result["speech"].lower()


async def test_lock_screen_linux_fallback_order(monkeypatch):
    fake_os(monkeypatch, power_module, "linux")
    only_binaries(monkeypatch, power_module, "loginctl", "xdg-screensaver")
    recorder = CommandRecorder(responses=[(1, "", "no session"), (0, "", "")])
    monkeypatch.setattr(power_module, "_run_command", recorder)

    result = await LockScreenTool()._run()
    assert recorder.calls == [
        ["loginctl", "lock-session"],
        ["xdg-screensaver", "lock"],
    ]
    assert result["command"] == "xdg-screensaver lock"


async def test_lock_screen_linux_gnome_screensaver(monkeypatch, power_run):
    fake_os(monkeypatch, power_module, "linux")
    only_binaries(monkeypatch, power_module, "gnome-screensaver-command")
    await LockScreenTool()._run()
    assert power_run.calls == [["gnome-screensaver-command", "-l"]]


async def test_lock_screen_linux_no_locker(monkeypatch, power_run):
    fake_os(monkeypatch, power_module, "linux")
    only_binaries(monkeypatch, power_module)
    result = await LockScreenTool().execute()
    assert result.success is False
    assert "loginctl" in result.error
    assert power_run.calls == []


# =============================================================================
# Power: sleep / shutdown / restart / cancel argv per OS
# =============================================================================


@pytest.mark.parametrize(
    "os_name,expected",
    [
        ("windows", ["rundll32", "powrprof.dll,SetSuspendState", "0,1,0"]),
        ("linux", ["systemctl", "suspend"]),
        ("macos", ["pmset", "sleepnow"]),
    ],
)
async def test_sleep_argv_per_os(monkeypatch, power_run, power_enabled, os_name, expected):
    fake_os(monkeypatch, power_module, os_name)
    result = await SleepTool()._run()
    assert power_run.calls == [expected]
    assert "sleep" in result["speech"].lower()


async def test_shutdown_windows_argv_and_default_delay(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "windows")
    result = await ShutdownTool()._run()
    assert power_run.calls == [["shutdown", "/s", "/t", str(DEFAULT_SHUTDOWN_DELAY_SECONDS)]]
    assert result["delay_seconds"] == DEFAULT_SHUTDOWN_DELAY_SECONDS
    assert "cancel" in result["speech"].lower()


async def test_shutdown_delay_floored_at_minimum(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "windows")
    await ShutdownTool()._run(delay_seconds=0)
    assert power_run.calls == [["shutdown", "/s", "/t", str(MIN_SHUTDOWN_DELAY_SECONDS)]]


async def test_shutdown_rejects_non_numeric_delay(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "windows")
    with pytest.raises(ToolError):
        await ShutdownTool()._run(delay_seconds="soon")
    assert power_run.calls == []


async def test_shutdown_linux_converts_seconds_to_minutes(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "linux")
    result = await ShutdownTool()._run(delay_seconds=90)
    assert power_run.calls == [["shutdown", "-h", "+2"]]  # 90s rounds up to 2 min
    assert result["delay_seconds"] == 120  # the real scheduled delay

    power_run.calls.clear()
    result = await ShutdownTool()._run()  # default 15s -> minimum 1 minute
    assert power_run.calls == [["shutdown", "-h", "+1"]]
    assert result["delay_seconds"] == 60


async def test_shutdown_macos_detached_osascript(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "macos")
    spawned: list[list[str]] = []
    monkeypatch.setattr(power_module, "_spawn_detached", lambda argv: spawned.append(list(argv)))

    result = await ShutdownTool()._run(delay_seconds=30)
    assert spawned == [
        ["osascript", "-e", "delay 30 -- iris-power",
         "-e", 'tell application "System Events" to shut down']
    ]
    assert power_run.calls == []  # detached, not run-and-wait
    assert result["delay_seconds"] == 30


async def test_restart_argv_per_os(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "windows")
    result = await RestartTool()._run(delay_seconds=20)
    assert power_run.calls == [["shutdown", "/r", "/t", "20"]]
    assert "restart" in result["speech"].lower()

    power_run.calls.clear()
    fake_os(monkeypatch, power_module, "linux")
    await RestartTool()._run(delay_seconds=61)
    assert power_run.calls == [["shutdown", "-r", "+2"]]

    spawned: list[list[str]] = []
    monkeypatch.setattr(power_module, "_spawn_detached", lambda argv: spawned.append(list(argv)))
    fake_os(monkeypatch, power_module, "macos")
    await RestartTool()._run(delay_seconds=10)
    assert spawned == [
        ["osascript", "-e", "delay 10 -- iris-power",
         "-e", 'tell application "System Events" to restart']
    ]


async def test_cancel_shutdown_argv_per_os(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "windows")
    result = await CancelShutdownTool()._run()
    assert power_run.calls == [["shutdown", "/a"]]
    assert result["cancelled"] is True

    power_run.calls.clear()
    fake_os(monkeypatch, power_module, "linux")
    await CancelShutdownTool()._run()
    assert power_run.calls == [["shutdown", "-c"]]

    power_run.calls.clear()
    fake_os(monkeypatch, power_module, "macos")
    await CancelShutdownTool()._run()
    # The pattern is the unique marker embedded in the scheduled osascript,
    # and must not start with '-' (pkill would parse it as an option).
    assert power_run.calls == [["pkill", "-f", "iris-power"]]
    assert not power_run.calls[0][-1].startswith("-")


async def test_cancel_shutdown_reports_nothing_pending(monkeypatch, power_enabled):
    fake_os(monkeypatch, power_module, "linux")
    recorder = CommandRecorder(default=(1, "", "shutdown: no shutdown scheduled"))
    monkeypatch.setattr(power_module, "_run_command", recorder)

    result = await CancelShutdownTool().execute()
    assert result.success is True  # a no-op, not a failure
    assert result.result["cancelled"] is False
    assert "no pending" in result.speech.lower()


async def test_power_unknown_os_rejected(monkeypatch, power_run, power_enabled):
    fake_os(monkeypatch, power_module, "unknown")
    for tool in (SleepTool(), ShutdownTool(), RestartTool(), CancelShutdownTool()):
        result = await tool.execute()
        assert result.success is False
    assert power_run.calls == []


async def test_power_command_failure_becomes_tool_error(monkeypatch, power_enabled):
    fake_os(monkeypatch, power_module, "linux")
    recorder = CommandRecorder(default=(1, "", "Failed to suspend"))
    monkeypatch.setattr(power_module, "_run_command", recorder)

    result = await SleepTool().execute()
    assert result.success is False
    assert "Failed to suspend" in result.error


# =============================================================================
# Metadata
# =============================================================================


def test_media_tool_metadata():
    tools = {tool.name: tool for tool in media_module.get_tools()}
    assert set(tools) == {"volume", "media_control"}

    volume = tools["volume"]
    assert volume.permission_level == PermissionLevel.DESKTOP_ACTION
    assert volume.category == "media"
    assert set(volume.aliases) == {"set_volume", "volume_up", "volume_down", "mute"}
    assert volume.mutating is True
    assert volume.required_capabilities == ()  # runtime fallbacks instead

    control = tools["media_control"]
    assert control.permission_level == PermissionLevel.DESKTOP_ACTION
    assert control.category == "media"
    assert set(control.aliases) == {
        "play_pause", "next_track", "previous_track",
        "pause_music", "play_music", "stop_music",
    }

    for tool in tools.values():
        assert tool.examples
        assert tool.input_schema.properties
        assert tool.get_metadata().available is True


def test_power_tool_metadata():
    tools = {tool.name: tool for tool in power_module.get_tools()}
    assert set(tools) == {
        "lock_screen", "sleep_pc", "shutdown_pc", "restart_pc", "cancel_shutdown",
    }

    assert tools["lock_screen"].permission_level == PermissionLevel.DESKTOP_ACTION
    assert tools["sleep_pc"].permission_level == PermissionLevel.CONFIRM_REQUIRED
    assert tools["shutdown_pc"].permission_level == PermissionLevel.HIGH_RISK_ACTION
    assert tools["restart_pc"].permission_level == PermissionLevel.HIGH_RISK_ACTION
    assert tools["cancel_shutdown"].permission_level == PermissionLevel.DESKTOP_ACTION

    assert set(tools["sleep_pc"].aliases) >= {"suspend", "sleep_computer"}
    assert set(tools["shutdown_pc"].aliases) >= {"shutdown", "power_off"}
    assert "reboot" in tools["restart_pc"].aliases

    for tool in tools.values():
        assert tool.mutating is True
        assert tool.examples
        assert tool.get_metadata().available is True  # all-OS, no hard capabilities
