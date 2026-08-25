"""Tests for the desktop input-control and clipboard tools.

The test environment is headless Linux with neither ``pyautogui`` nor
``pyperclip`` (nor any clipboard binary) installed, so these tests cover:

* the pure key-validation logic (:func:`parse_key_combo` / :func:`normalize_key`),
* ``_run``-level behavior against a fake pyautogui module,
* graceful ``execute()`` failures with install hints when GUI deps are absent,
* the clipboard fallback chain with monkeypatched ``shutil.which`` and
  ``subprocess.run``, including truncation of oversized reads.
"""

from __future__ import annotations

import subprocess

import pytest

from iris.app.core.security import PermissionLevel
from iris.app.tools.base import ToolError
from iris.app.tools.desktop import clipboard as clipboard_module
from iris.app.tools.desktop import input_control
from iris.app.tools.desktop.clipboard import (
    ClipboardReadTool,
    ClipboardWriteTool,
    MAX_CLIPBOARD_READ_CHARS,
    read_clipboard_text,
    write_clipboard_text,
)
from iris.app.tools.desktop.input_control import (
    KEY_ALLOWLIST,
    MouseClickTool,
    MouseMoveTool,
    PressKeysTool,
    ScreenSizeTool,
    ScrollTool,
    TypeTextTool,
    normalize_key,
    parse_key_combo,
)


# =============================================================================
# Fakes
# =============================================================================


class FakePyAutoGUI:
    """Records every call so tests can assert exact automation behavior."""

    def __init__(self, width: int = 1920, height: int = 1080, pos: tuple[int, int] = (100, 200)):
        self.FAILSAFE = False
        self.calls: list[tuple] = []
        self._size = (width, height)
        self._pos = pos

    def write(self, text, interval=0.0):
        self.calls.append(("write", text, interval))

    def press(self, key, presses=1, interval=0.0):
        self.calls.append(("press", key, presses))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    def click(self, x=None, y=None, button="left"):
        self.calls.append(("click", x, y, button))

    def doubleClick(self, x=None, y=None):  # noqa: N802 - mirrors pyautogui API
        self.calls.append(("doubleClick", x, y))

    def moveTo(self, x, y, duration=0.0):  # noqa: N802 - mirrors pyautogui API
        self.calls.append(("moveTo", x, y, duration))
        self._pos = (x, y)

    def scroll(self, clicks):
        self.calls.append(("scroll", clicks))

    def size(self):
        return self._size

    def position(self):
        return self._pos


@pytest.fixture
def fake_pag(monkeypatch):
    """Route input_control's pyautogui import to a recording fake."""
    fake = FakePyAutoGUI()
    monkeypatch.setattr(input_control, "try_import", lambda name: fake)
    return fake


@pytest.fixture
def no_clipboard_providers(monkeypatch):
    """Simulate a machine with no pyperclip and no clipboard binaries."""
    monkeypatch.setattr(clipboard_module, "try_import", lambda name: None)
    monkeypatch.setattr(clipboard_module.shutil, "which", lambda name: None)


# =============================================================================
# Key validation logic
# =============================================================================


def test_parse_simple_combo():
    assert parse_key_combo("ctrl+shift+t") == ["ctrl", "shift", "t"]


def test_parse_single_key_and_case():
    assert parse_key_combo("ENTER") == ["enter"]
    assert parse_key_combo("Tab") == ["tab"]


def test_parse_whitespace_tolerant():
    assert parse_key_combo(" Ctrl + Alt + Del ") == ["ctrl", "alt", "delete"]


def test_aliases_resolve_to_canonical_names():
    assert normalize_key("Return") == "enter"
    assert normalize_key("escape") == "esc"
    assert normalize_key("cmd") == "command"
    assert normalize_key("windows") == "win"
    assert normalize_key("super") == "win"
    assert normalize_key("option") == "alt"
    assert normalize_key("pgdn") == "pagedown"
    assert normalize_key("Page Up") == "pageup"
    assert normalize_key("print screen") == "printscreen"
    assert normalize_key("arrow_up") == "up"
    assert normalize_key("plus") == "+"
    assert normalize_key("menu") == "apps"


def test_single_characters_pass_through():
    assert normalize_key("T") == "t"
    assert normalize_key("-") == "-"
    assert normalize_key("_") == "_"


def test_literal_plus_key():
    assert parse_key_combo("+") == ["+"]
    assert parse_key_combo("ctrl++") == ["ctrl", "+"]
    assert parse_key_combo("ctrl++v") == ["ctrl", "+", "v"]


def test_function_keys_range():
    assert parse_key_combo("f1") == ["f1"]
    assert parse_key_combo("f24") == ["f24"]
    with pytest.raises(ToolError):
        parse_key_combo("f25")


def test_media_and_system_keys_allowed():
    for key in ("volumeup", "volumedown", "volumemute", "playpause",
                "nexttrack", "prevtrack", "printscreen", "insert"):
        assert parse_key_combo(key) == [key]


def test_allowlist_contains_expected_families():
    assert "a" in KEY_ALLOWLIST and "z" in KEY_ALLOWLIST
    assert "0" in KEY_ALLOWLIST and "9" in KEY_ALLOWLIST
    assert "f12" in KEY_ALLOWLIST and "f25" not in KEY_ALLOWLIST
    assert ";" in KEY_ALLOWLIST and "/" in KEY_ALLOWLIST
    assert "pageup" in KEY_ALLOWLIST and "command" in KEY_ALLOWLIST


def test_unknown_key_rejected_with_examples():
    with pytest.raises(ToolError) as excinfo:
        parse_key_combo("ctrl+banana")
    message = str(excinfo.value)
    assert "banana" in message
    assert "enter" in message  # valid examples are listed
    assert "ctrl" in message


def test_multiple_unknown_keys_all_listed():
    with pytest.raises(ToolError) as excinfo:
        parse_key_combo("foo+bar+ctrl")
    message = str(excinfo.value)
    assert "foo" in message and "bar" in message


def test_empty_combo_rejected():
    with pytest.raises(ToolError):
        parse_key_combo("")
    with pytest.raises(ToolError):
        parse_key_combo("   ")


# =============================================================================
# Input tools: _run behavior against the fake pyautogui
# =============================================================================


async def test_type_text_runs_and_reenables_failsafe(fake_pag):
    result = await TypeTextTool()._run(text="hello", press_enter=True)
    assert ("write", "hello", 0.02) in fake_pag.calls
    assert ("press", "enter", 1) in fake_pag.calls
    assert fake_pag.FAILSAFE is True
    assert result["typed_characters"] == 5
    assert result["pressed_enter"] is True
    assert "speech" in result and "enter" in result["speech"]


async def test_type_text_interval_clamped(fake_pag):
    await TypeTextTool()._run(text="hi", interval=9.0)
    assert ("write", "hi", 1.0) in fake_pag.calls


async def test_type_text_requires_text(fake_pag):
    with pytest.raises(ToolError):
        await TypeTextTool()._run(text="")
    with pytest.raises(ToolError):
        await TypeTextTool()._run()


async def test_press_keys_single_key_uses_press(fake_pag):
    result = await PressKeysTool()._run(keys="enter", presses=3)
    assert ("press", "enter", 3) in fake_pag.calls
    assert result["presses"] == 3
    assert result["combo"] == "enter"


async def test_press_keys_combo_uses_hotkey(fake_pag):
    result = await PressKeysTool()._run(keys="ctrl+shift+t")
    assert ("hotkey", ("ctrl", "shift", "t")) in fake_pag.calls
    assert result["combo"] == "ctrl+shift+t"
    assert "Pressed" in result["speech"]


async def test_press_keys_rejects_unknown_and_bad_presses(fake_pag):
    with pytest.raises(ToolError):
        await PressKeysTool()._run(keys="ctrl+banana")
    with pytest.raises(ToolError):
        await PressKeysTool()._run(keys="enter", presses=0)
    with pytest.raises(ToolError):
        await PressKeysTool()._run(keys="enter", presses=999)
    assert fake_pag.calls == []  # nothing was pressed


async def test_mouse_click_at_coordinates(fake_pag):
    result = await MouseClickTool()._run(x=10, y=20, button="right")
    assert ("click", 10, 20, "right") in fake_pag.calls
    assert result["button"] == "right"


async def test_mouse_click_double(fake_pag):
    await MouseClickTool()._run(x=10, y=20, button="double")
    assert ("doubleClick", 10, 20) in fake_pag.calls


async def test_mouse_click_current_position(fake_pag):
    result = await MouseClickTool()._run()
    assert ("click", None, None, "left") in fake_pag.calls
    assert result["x"] == 100 and result["y"] == 200  # fake pointer position


async def test_mouse_click_validation(fake_pag):
    with pytest.raises(ToolError):
        await MouseClickTool()._run(button="nope")
    with pytest.raises(ToolError):
        await MouseClickTool()._run(x=10)  # y missing
    with pytest.raises(ToolError):
        await MouseClickTool()._run(x=5000, y=10)  # off screen


async def test_mouse_move(fake_pag):
    result = await MouseMoveTool()._run(x=300, y=400, duration=0.5)
    assert ("moveTo", 300, 400, 0.5) in fake_pag.calls
    assert result["speech"] == "Moved the mouse to (300, 400)."


async def test_mouse_move_validation(fake_pag):
    with pytest.raises(ToolError):
        await MouseMoveTool()._run(x=300)  # y missing
    with pytest.raises(ToolError):
        await MouseMoveTool()._run(x=300, y=9000)  # off screen


async def test_scroll_direction_sign(fake_pag):
    await ScrollTool()._run(direction="down", amount=5)
    assert ("scroll", -5) in fake_pag.calls
    await ScrollTool()._run(direction="up", amount=3)
    assert ("scroll", 3) in fake_pag.calls


async def test_scroll_validation(fake_pag):
    with pytest.raises(ToolError):
        await ScrollTool()._run(direction="sideways")
    with pytest.raises(ToolError):
        await ScrollTool()._run(direction="up", amount=0)


async def test_screen_size(fake_pag):
    result = await ScreenSizeTool()._run()
    assert result["width"] == 1920 and result["height"] == 1080
    assert result["mouse_x"] == 100 and result["mouse_y"] == 200
    assert "1920 by 1080" in result["speech"]


# =============================================================================
# Graceful unavailability on this headless box (no pyautogui installed)
# =============================================================================


@pytest.mark.parametrize(
    "tool_factory,kwargs",
    [
        (TypeTextTool, {"text": "hello"}),
        (PressKeysTool, {"keys": "enter"}),
        (MouseClickTool, {}),
        (MouseMoveTool, {"x": 1, "y": 1}),
        (ScrollTool, {"direction": "down"}),
        (ScreenSizeTool, {}),
    ],
)
async def test_gui_tools_unavailable_headless(tool_factory, kwargs):
    tool = tool_factory()
    result = await tool.execute(**kwargs)
    assert result.success is False
    assert "gui_input" in result.error
    assert "pip install pyautogui" in result.error
    assert result.speech  # spoken form always present


def test_input_tool_metadata():
    tools = {tool.name: tool for tool in input_control.get_tools()}
    assert set(tools) == {
        "type_text", "press_keys", "mouse_click", "mouse_move", "scroll", "screen_size",
    }
    for tool in tools.values():
        assert tool.required_capabilities == ("gui_input",)
        assert tool.category == "desktop"
        assert tool.examples  # every tool documents examples
        meta = tool.get_metadata()
        assert meta.available is False  # headless box
        assert meta.unavailable_reason

    assert tools["type_text"].permission_level == PermissionLevel.DESKTOP_ACTION
    assert tools["type_text"].mutating is True
    assert "keyboard_type" in tools["type_text"].aliases
    assert "write_text" in tools["type_text"].aliases
    assert set(tools["press_keys"].aliases) >= {"hotkey", "shortcut", "key_combo"}
    assert tools["screen_size"].permission_level == PermissionLevel.READ
    assert tools["screen_size"].mutating is False


# =============================================================================
# Clipboard fallback chain
# =============================================================================


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_read_uses_pyperclip_first(monkeypatch):
    class FakePyperclip:
        @staticmethod
        def paste():
            return "from-pyperclip"

    monkeypatch.setattr(clipboard_module, "try_import", lambda name: FakePyperclip)
    monkeypatch.setattr(
        clipboard_module.shutil, "which",
        lambda name: pytest.fail("binaries must not be probed when pyperclip works"),
    )
    assert read_clipboard_text() == "from-pyperclip"


def test_read_falls_back_when_pyperclip_raises(monkeypatch):
    class BrokenPyperclip:
        @staticmethod
        def paste():
            raise RuntimeError("no backend")

    monkeypatch.setattr(clipboard_module, "try_import", lambda name: BrokenPyperclip)
    monkeypatch.setattr(
        clipboard_module.shutil, "which",
        lambda name: "/usr/bin/xclip" if name == "xclip" else None,
    )
    monkeypatch.setattr(
        clipboard_module.subprocess, "run",
        lambda argv, **kw: _completed(argv, 0, stdout="fallback"),
    )
    assert read_clipboard_text() == "fallback"


def test_read_binary_chain_order_and_failover(monkeypatch):
    calls: list[str] = []

    def fake_which(name):
        return f"/usr/bin/{name}" if name in {"wl-paste", "xclip"} else None

    def fake_run(argv, **kwargs):
        calls.append(argv[0])
        if argv[0] == "wl-paste":
            return _completed(argv, 1, stderr="no wayland compositor")
        return _completed(argv, 0, stdout="from-xclip")

    monkeypatch.setattr(clipboard_module, "try_import", lambda name: None)
    monkeypatch.setattr(clipboard_module.shutil, "which", fake_which)
    monkeypatch.setattr(clipboard_module.subprocess, "run", fake_run)

    assert read_clipboard_text() == "from-xclip"
    assert calls == ["wl-paste", "xclip"]  # wl-paste tried first, then xclip


def test_read_xclip_argv_targets_clipboard_selection(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _completed(argv, 0, stdout="x")

    monkeypatch.setattr(clipboard_module, "try_import", lambda name: None)
    monkeypatch.setattr(
        clipboard_module.shutil, "which",
        lambda name: "/usr/bin/xclip" if name == "xclip" else None,
    )
    monkeypatch.setattr(clipboard_module.subprocess, "run", fake_run)

    read_clipboard_text()
    assert seen["argv"] == ["xclip", "-selection", "clipboard", "-o"]


def test_write_via_binary_passes_text_on_stdin(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        return _completed(argv, 0)

    monkeypatch.setattr(clipboard_module, "try_import", lambda name: None)
    monkeypatch.setattr(
        clipboard_module.shutil, "which",
        lambda name: "/usr/bin/xsel" if name == "xsel" else None,
    )
    monkeypatch.setattr(clipboard_module.subprocess, "run", fake_run)

    backend = write_clipboard_text("hello there")
    assert backend == "xsel"
    assert seen["argv"] == ["xsel", "--clipboard", "--input"]
    assert seen["input"] == "hello there"


def test_write_uses_pyperclip_first(monkeypatch):
    copied: list[str] = []

    class FakePyperclip:
        @staticmethod
        def copy(value):
            copied.append(value)

    monkeypatch.setattr(clipboard_module, "try_import", lambda name: FakePyperclip)
    assert write_clipboard_text("abc") == "pyperclip"
    assert copied == ["abc"]


def test_read_and_write_raise_with_hint_when_nothing_available(no_clipboard_providers):
    with pytest.raises(ToolError) as excinfo:
        read_clipboard_text()
    assert "pyperclip" in str(excinfo.value)
    with pytest.raises(ToolError) as excinfo:
        write_clipboard_text("x")
    assert "pyperclip" in str(excinfo.value)


def test_read_survives_oserror_from_binary(monkeypatch):
    def fake_run(argv, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(clipboard_module, "try_import", lambda name: None)
    monkeypatch.setattr(clipboard_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(clipboard_module.subprocess, "run", fake_run)

    with pytest.raises(ToolError):
        read_clipboard_text()


# =============================================================================
# Clipboard tools: execute()-level behavior
# =============================================================================


async def test_clipboard_read_tool_success(monkeypatch):
    monkeypatch.setattr(clipboard_module, "read_clipboard_text", lambda: "hello world")
    result = await ClipboardReadTool().execute()
    assert result.success is True
    assert result.result["text"] == "hello world"
    assert result.result["truncated"] is False
    assert "hello world" in result.speech


async def test_clipboard_read_tool_truncates_large_content(monkeypatch):
    big = "x" * (MAX_CLIPBOARD_READ_CHARS + 50_000)
    monkeypatch.setattr(clipboard_module, "read_clipboard_text", lambda: big)
    result = await ClipboardReadTool().execute()
    assert result.success is True
    assert result.result["truncated"] is True
    assert len(result.result["text"]) == MAX_CLIPBOARD_READ_CHARS
    assert result.result["length"] == len(big)
    assert "note" in result.result and str(len(big)) in result.result["note"]


async def test_clipboard_read_tool_unavailable_headless(no_clipboard_providers):
    result = await ClipboardReadTool().execute()
    assert result.success is False
    assert "pyperclip" in result.error  # install hint present
    assert result.speech


async def test_clipboard_write_tool_success(monkeypatch):
    written: list[str] = []

    def fake_write(text):
        written.append(text)
        return "xclip"

    monkeypatch.setattr(clipboard_module, "write_clipboard_text", fake_write)
    result = await ClipboardWriteTool().execute(text="copy me")
    assert result.success is True
    assert written == ["copy me"]
    assert result.result["backend"] == "xclip"
    assert "Copied 7 characters" in result.speech


async def test_clipboard_write_tool_requires_text(no_clipboard_providers):
    result = await ClipboardWriteTool().execute()
    assert result.success is False
    assert "text" in result.error


async def test_clipboard_write_tool_unavailable_headless(no_clipboard_providers):
    result = await ClipboardWriteTool().execute(text="hello")
    assert result.success is False
    assert "pyperclip" in result.error


def test_clipboard_tool_metadata():
    tools = {tool.name: tool for tool in clipboard_module.get_tools()}
    assert set(tools) == {"clipboard_read", "clipboard_write"}

    read_tool = tools["clipboard_read"]
    assert read_tool.permission_level == PermissionLevel.READ
    assert read_tool.mutating is False
    assert set(read_tool.aliases) == {"get_clipboard", "paste_from_clipboard"}
    assert read_tool.required_capabilities == ()  # runtime fallbacks instead

    write_tool = tools["clipboard_write"]
    assert write_tool.permission_level == PermissionLevel.LOW_RISK_ACTION
    assert write_tool.mutating is True
    assert set(write_tool.aliases) == {"set_clipboard", "copy_to_clipboard"}
    assert write_tool.required_capabilities == ()

    for tool in tools.values():
        assert tool.examples
        # No declared capabilities means the registry always lists them as
        # available; the runtime chain reports the real story.
        assert tool.get_metadata().available is True
