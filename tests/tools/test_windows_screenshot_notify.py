"""Tests for the window-management, screenshot and notification tools.

The test environment is headless Linux with none of the optional GUI
dependencies (pygetwindow, mss, Pillow, plyer) or helper binaries (wmctrl,
xdotool, scrot, notify-send) installed, so these tests cover:

* the pure matching / sanitization logic (:func:`best_match`,
  :func:`sanitize_filename`),
* backend strategy selection and per-tool behavior against a fake backend
  injected by monkeypatching :func:`select_backend`,
* the concrete ``wmctrl`` backend with monkeypatched ``subprocess.run``,
* the screenshot and notification fallback chains with monkeypatched
  ``try_import`` / ``shutil.which`` / ``subprocess.run`` — including the
  Windows BurntToast / msg.exe branches,
* graceful ``execute()`` failures with install hints on a headless box.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris.app.core.security import PermissionLevel
from iris.app.tools.base import ToolError
from iris.app.tools.desktop import notify as notify_module
from iris.app.tools.desktop import screenshot as screenshot_module
from iris.app.tools.desktop import windows_mgmt
from iris.app.tools.desktop.notify import NotifyTool, send_notification
from iris.app.tools.desktop.screenshot import (
    ScreenshotTool,
    capture_screenshot,
    sanitize_filename,
)
from iris.app.tools.desktop.windows_mgmt import (
    CloseWindowTool,
    FocusWindowTool,
    ListWindowsTool,
    MaximizeWindowTool,
    MinimizeWindowTool,
    WmctrlBackend,
    best_match,
    select_backend,
)


def completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


# =============================================================================
# Fakes
# =============================================================================


class FakeBackend:
    """Records every window action so tests can assert exact behavior."""

    name = "fake"

    def __init__(self, titles):
        self.titles = list(titles)
        self.calls: list[tuple[str, str]] = []

    def list_titles(self):
        return list(self.titles)

    def activate(self, title):
        self.calls.append(("activate", title))

    def minimize(self, title):
        self.calls.append(("minimize", title))

    def maximize(self, title):
        self.calls.append(("maximize", title))

    def close(self, title):
        self.calls.append(("close", title))


@pytest.fixture
def fake_backend(monkeypatch):
    """Route every window tool to a recording fake backend."""
    fake = FakeBackend(["Mozilla Firefox", "Terminal — bash", "Files", "Firefox Downloads"])
    monkeypatch.setattr(windows_mgmt, "select_backend", lambda: fake)
    return fake


@pytest.fixture
def no_window_backends(monkeypatch):
    """Simulate a machine with no pygetwindow and no window binaries."""
    monkeypatch.setattr(windows_mgmt, "try_import", lambda name: None)
    monkeypatch.setattr(windows_mgmt.shutil, "which", lambda name: None)


# =============================================================================
# best_match — pure matching logic
# =============================================================================


def test_best_match_case_insensitive_substring():
    titles = ["Terminal — bash", "Mozilla Firefox", "Files"]
    assert best_match(titles, "FIREFOX") == "Mozilla Firefox"


def test_best_match_exact_beats_substring():
    titles = ["Firefox Downloads", "Firefox"]
    assert best_match(titles, "firefox") == "Firefox"


def test_best_match_prefix_beats_interior():
    titles = ["My Firefox Notes", "Firefox Downloads"]
    assert best_match(titles, "firefox") == "Firefox Downloads"


def test_best_match_shorter_title_wins_ties():
    titles = ["Notes — long document title", "Notes"]
    assert best_match(titles, "notes") == "Notes"


def test_best_match_none_when_absent_or_empty():
    titles = ["Terminal", "Files"]
    assert best_match(titles, "chrome") is None
    assert best_match(titles, "") is None
    assert best_match(titles, "   ") is None
    assert best_match([], "anything") is None


# =============================================================================
# Window tools through a fake strategy backend
# =============================================================================


async def test_list_windows_reports_titles(fake_backend):
    result = await ListWindowsTool().execute()
    assert result.success
    assert result.result["windows"] == fake_backend.titles
    assert result.result["count"] == 4
    assert result.result["backend"] == "fake"
    assert "4 windows" in result.speech


async def test_focus_window_picks_best_match(fake_backend):
    # "firefox" prefix-matches "Firefox Downloads" (prefix beats interior match).
    result = await FocusWindowTool().execute(title="firefox")
    assert result.success
    assert fake_backend.calls == [("activate", "Firefox Downloads")]
    assert result.result["window"] == "Firefox Downloads"
    assert "Firefox Downloads" in result.speech

    # An interior match still works when it is the only match.
    result = await FocusWindowTool().execute(title="mozilla")
    assert result.success
    assert fake_backend.calls[-1] == ("activate", "Mozilla Firefox")


async def test_minimize_maximize_close_dispatch(fake_backend):
    assert (await MinimizeWindowTool().execute(title="terminal")).success
    assert (await MaximizeWindowTool().execute(title="files")).success
    assert (await CloseWindowTool().execute(title="bash")).success
    assert fake_backend.calls == [
        ("minimize", "Terminal — bash"),
        ("maximize", "Files"),
        ("close", "Terminal — bash"),
    ]


async def test_focus_window_no_match_lists_candidates(fake_backend):
    result = await FocusWindowTool().execute(title="spotify")
    assert not result.success
    assert "spotify" in result.error
    assert "Mozilla Firefox" in result.error  # candidates are suggested
    assert fake_backend.calls == []


async def test_focus_window_requires_title(fake_backend):
    result = await FocusWindowTool().execute(title="   ")
    assert not result.success
    assert "title" in result.error.lower()


def test_window_tool_metadata():
    assert ListWindowsTool.permission_level == PermissionLevel.READ
    assert FocusWindowTool.permission_level == PermissionLevel.DESKTOP_ACTION
    assert CloseWindowTool.permission_level == PermissionLevel.CONFIRM_REQUIRED
    assert "switch_to" in FocusWindowTool.aliases
    assert "activate_window" in FocusWindowTool.aliases
    for tool_cls in (ListWindowsTool, FocusWindowTool, MinimizeWindowTool,
                     MaximizeWindowTool, CloseWindowTool):
        assert tool_cls.required_capabilities == ()  # runtime fallbacks instead
        assert tool_cls.examples


# =============================================================================
# Backend selection
# =============================================================================


def test_select_backend_without_providers_mentions_wmctrl(no_window_backends):
    with pytest.raises(ToolError) as excinfo:
        select_backend()
    assert "wmctrl" in str(excinfo.value)


async def test_list_windows_headless_failure(no_window_backends):
    result = await ListWindowsTool().execute()
    assert not result.success
    assert "wmctrl" in result.error


def test_select_backend_prefers_pygetwindow(monkeypatch):
    fake_gw = SimpleNamespace(getAllTitles=lambda: ["A"], getWindowsWithTitle=lambda t: [])
    monkeypatch.setattr(
        windows_mgmt, "try_import", lambda name: fake_gw if name == "pygetwindow" else None
    )
    backend = select_backend()
    assert backend.name == "pygetwindow"
    assert backend.list_titles() == ["A"]


def test_select_backend_picks_wmctrl_then_xdotool(monkeypatch):
    monkeypatch.setattr(windows_mgmt, "try_import", lambda name: None)
    monkeypatch.setattr(
        windows_mgmt.shutil, "which", lambda name: "/usr/bin/wmctrl" if name == "wmctrl" else None
    )
    assert select_backend().name == "wmctrl"
    monkeypatch.setattr(
        windows_mgmt.shutil, "which", lambda name: "/usr/bin/xdotool" if name == "xdotool" else None
    )
    assert select_backend().name == "xdotool"


# =============================================================================
# WmctrlBackend against a monkeypatched subprocess
# =============================================================================

_WMCTRL_LIST = (
    "0x04000003 -1 host Desktop\n"          # desktop -1 => skipped (panel/desktop)
    "0x04800003  0 host Mozilla Firefox\n"
    "0x05000005  1 host Terminal — bash\n"
)


@pytest.fixture
def wmctrl_calls(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["wmctrl", "-l"]:
            return completed(argv, stdout=_WMCTRL_LIST)
        return completed(argv)

    monkeypatch.setattr(windows_mgmt.subprocess, "run", fake_run)
    monkeypatch.setattr(windows_mgmt.shutil, "which", lambda name: None)
    return calls


def test_wmctrl_lists_and_skips_sticky_windows(wmctrl_calls):
    assert WmctrlBackend().list_titles() == ["Mozilla Firefox", "Terminal — bash"]


def test_wmctrl_acts_by_window_id(wmctrl_calls):
    backend = WmctrlBackend()
    backend.activate("Mozilla Firefox")
    assert wmctrl_calls[-1] == ["wmctrl", "-i", "-a", "0x04800003"]
    backend.close("Terminal — bash")
    assert wmctrl_calls[-1] == ["wmctrl", "-i", "-c", "0x05000005"]
    backend.maximize("Mozilla Firefox")
    assert wmctrl_calls[-1] == [
        "wmctrl", "-i", "-r", "0x04800003", "-b", "add,maximized_vert,maximized_horz",
    ]
    backend.minimize("Mozilla Firefox")  # no xdotool on PATH -> wmctrl hidden state
    assert wmctrl_calls[-1] == ["wmctrl", "-i", "-r", "0x04800003", "-b", "add,hidden"]


def test_wmctrl_minimize_prefers_xdotool(wmctrl_calls, monkeypatch):
    monkeypatch.setattr(
        windows_mgmt.shutil, "which", lambda name: "/usr/bin/xdotool" if name == "xdotool" else None
    )
    WmctrlBackend().minimize("Mozilla Firefox")
    assert wmctrl_calls[-1] == ["xdotool", "windowminimize", "0x04800003"]


def test_wmctrl_reports_helper_failure(monkeypatch):
    monkeypatch.setattr(
        windows_mgmt.subprocess,
        "run",
        lambda argv, **kwargs: completed(argv, returncode=1, stderr="Cannot open display"),
    )
    with pytest.raises(ToolError) as excinfo:
        WmctrlBackend().list_titles()
    assert "Cannot open display" in str(excinfo.value)


# =============================================================================
# Screenshot — filename sanitization (pure logic)
# =============================================================================


def test_sanitize_filename_defaults_to_timestamp():
    name = sanitize_filename(None)
    assert name.startswith("screenshot_")
    assert name.endswith(".png")
    assert sanitize_filename("   ").startswith("screenshot_")


def test_sanitize_filename_strips_directories():
    assert sanitize_filename("../../etc/passwd") == "passwd.png"
    assert sanitize_filename("C:\\Users\\me\\shot.png") == "shot.png"
    assert sanitize_filename("/absolute/path/pic") == "pic.png"


def test_sanitize_filename_replaces_unsafe_chars():
    assert sanitize_filename("weird*name?") == "weird_name.png"
    assert sanitize_filename("a;rm -rf$(x)") == "a_rm -rf_x.png"


def test_sanitize_filename_single_png_extension():
    assert sanitize_filename("shot.png") == "shot.png"
    assert sanitize_filename("shot.PNG") == "shot.png"
    assert sanitize_filename("before-update") == "before-update.png"


def test_sanitize_filename_bounds_length():
    assert len(sanitize_filename("x" * 500)) <= 124


# =============================================================================
# Screenshot — backend chain
# =============================================================================


class _OpenSandbox:
    """Sandbox stand-in that accepts any path (tests write into tmp_path)."""

    def resolve(self, path, **kwargs):
        return Path(path)


@pytest.fixture
def shots_dir(tmp_path, monkeypatch):
    """Redirect screenshots into tmp_path and bypass the real sandbox roots."""
    target = tmp_path / "shots"
    monkeypatch.setattr(screenshot_module.paths, "screenshots_dir", lambda: target)
    monkeypatch.setattr(screenshot_module, "default_path_sandbox", _OpenSandbox())
    return target


async def test_screenshot_headless_failure(shots_dir, monkeypatch):
    monkeypatch.setattr(screenshot_module, "try_import", lambda name: None)
    monkeypatch.setattr(screenshot_module.shutil, "which", lambda name: None)
    result = await ScreenshotTool().execute()
    assert not result.success
    assert "mss" in result.error
    assert "scrot" in result.error


async def test_screenshot_via_scrot(shots_dir, monkeypatch):
    monkeypatch.setattr(screenshot_module, "try_import", lambda name: None)
    monkeypatch.setattr(
        screenshot_module.shutil, "which", lambda name: "/usr/bin/scrot" if name == "scrot" else None
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        Path(argv[-1]).write_bytes(b"\x89PNG fake")
        return completed(argv)

    monkeypatch.setattr(screenshot_module.subprocess, "run", fake_run)

    result = await ScreenshotTool().execute(filename="before update!")
    assert result.success
    assert result.result["backend"] == "scrot"
    assert calls == [["scrot", str(shots_dir / "before update.png")]]
    saved = Path(result.result["file"])
    assert saved.exists() and saved.parent == shots_dir
    assert result.artifacts == [str(saved)]
    assert result.ui == {"preview": str(saved)}
    assert result.speech == "Screenshot saved."


async def test_screenshot_prefers_mss(shots_dir, monkeypatch):
    class FakeSct:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def shot(self, mon=-1, output=None):
            Path(output).write_bytes(b"\x89PNG fake")
            return output

    fake_mss = SimpleNamespace(mss=lambda: FakeSct())
    monkeypatch.setattr(
        screenshot_module, "try_import", lambda name: fake_mss if name == "mss" else None
    )
    result = await ScreenshotTool().execute()
    assert result.success
    assert result.result["backend"] == "mss"
    assert Path(result.result["file"]).name.startswith("screenshot_")


def test_screenshot_falls_through_broken_mss(shots_dir, monkeypatch):
    class BrokenSct:
        def __enter__(self):
            raise RuntimeError("no X display")

        def __exit__(self, *exc):
            return False

    fake_mss = SimpleNamespace(mss=lambda: BrokenSct())
    monkeypatch.setattr(
        screenshot_module, "try_import", lambda name: fake_mss if name == "mss" else None
    )
    monkeypatch.setattr(screenshot_module.shutil, "which", lambda name: None)
    with pytest.raises(ToolError) as excinfo:
        capture_screenshot(shots_dir.joinpath("x.png"))
    assert "no X display" in str(excinfo.value)


def test_screenshot_tool_metadata():
    assert ScreenshotTool.permission_level == PermissionLevel.READ
    assert set(ScreenshotTool.aliases) == {"screenshot", "capture_screen", "print_screen"}
    assert ScreenshotTool.required_capabilities == ()
    assert ScreenshotTool.examples


# =============================================================================
# Notify — fallback chain
# =============================================================================


async def test_notify_requires_message():
    result = await NotifyTool().execute(message="   ")
    assert not result.success
    assert "message" in result.error.lower()


async def test_notify_prefers_plyer(monkeypatch):
    recorded: dict = {}
    fake_plyer = SimpleNamespace(
        notification=SimpleNamespace(notify=lambda **kwargs: recorded.update(kwargs))
    )
    monkeypatch.setattr(
        notify_module, "try_import", lambda name: fake_plyer if name == "plyer" else None
    )
    result = await NotifyTool().execute(title="Build", message="The build finished.")
    assert result.success
    assert result.result["backend"] == "plyer"
    assert recorded["title"] == "Build"
    assert recorded["message"] == "The build finished."
    assert result.speech == "Notification sent."


async def test_notify_send_on_linux(monkeypatch):
    monkeypatch.setattr(notify_module, "try_import", lambda name: None)
    monkeypatch.setattr(
        notify_module.shutil,
        "which",
        lambda name: "/usr/bin/notify-send" if name == "notify-send" else None,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        notify_module.subprocess,
        "run",
        lambda argv, **kwargs: (calls.append(list(argv)), completed(argv))[1],
    )
    result = await NotifyTool().execute(message="Download complete.")
    assert result.success
    assert result.result["backend"] == "notify-send"
    assert result.result["title"] == "IRIS"  # default title
    assert calls == [["notify-send", "--app-name=IRIS", "--", "IRIS", "Download complete."]]


async def test_notify_headless_failure(monkeypatch):
    monkeypatch.setattr(notify_module, "try_import", lambda name: None)
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: None)
    result = await NotifyTool().execute(message="hello")
    assert not result.success
    assert "plyer" in result.error
    assert "notify-send" in result.error


def _fake_windows(monkeypatch):
    monkeypatch.setattr(notify_module, "try_import", lambda name: None)
    monkeypatch.setattr(notify_module, "is_linux", lambda: False)
    monkeypatch.setattr(notify_module, "is_macos", lambda: False)
    monkeypatch.setattr(notify_module, "is_windows", lambda: True)


def test_notify_windows_burnttoast(monkeypatch):
    _fake_windows(monkeypatch)
    monkeypatch.setattr(
        notify_module.shutil,
        "which",
        lambda name: "C:\\ps\\powershell.exe" if name == "powershell" else None,
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "Get-Module" in argv[-1]:
            return completed(argv, stdout="Script  0.8.5  BurntToast")
        return completed(argv)

    monkeypatch.setattr(notify_module.subprocess, "run", fake_run)
    assert send_notification("Build", "It's done") == "burnttoast"
    assert "New-BurntToastNotification -Text 'Build', 'It''s done'" in calls[-1][-1]


def test_notify_windows_msg_fallback(monkeypatch):
    _fake_windows(monkeypatch)
    monkeypatch.setattr(
        notify_module.shutil,
        "which",
        lambda name: f"C:\\bin\\{name}.exe" if name in ("powershell", "msg") else None,
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "Get-Module" in argv[-1]:
            return completed(argv, stdout="")  # BurntToast not installed
        return completed(argv)

    monkeypatch.setattr(notify_module.subprocess, "run", fake_run)
    assert send_notification("Build", "Done") == "msg.exe"
    assert calls[-1][:2] == ["msg", "*"]
    assert calls[-1][-1] == "Build: Done"


def test_notify_tool_metadata():
    assert NotifyTool.permission_level == PermissionLevel.LOW_RISK_ACTION
    assert set(NotifyTool.aliases) == {"notification", "alert", "toast"}
    assert NotifyTool.required_capabilities == ()
    assert NotifyTool.examples
