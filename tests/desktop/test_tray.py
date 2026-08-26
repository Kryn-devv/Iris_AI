"""Tests for the system tray wrapper (iris/app/desktop/tray.py).

Headless Linux with no pystray/Pillow installed, so ``try_import``,
``has_display`` and the icon image factory are monkeypatched with recording
fakes. The key behavior under test: pystray's darwin backend must never be
``run()`` in a secondary thread — macOS uses ``run_detached()`` when the
build provides it and otherwise skips the tray entirely.
"""

from __future__ import annotations

import pytest

from iris.app.desktop import tray as tray_module
from iris.app.desktop.tray import TrayIcon


class FakeIcon:
    """Records how pystray.Icon is driven without any GUI."""

    detachable = True

    def __init__(self, name, image, title, menu):
        self.name = name
        self.run_calls = 0
        self.detached_calls = 0
        self.stopped = False
        if not self.detachable:
            # Simulate a pystray build without run_detached().
            self.run_detached = None  # type: ignore[assignment]

    def run(self):
        self.run_calls += 1

    def run_detached(self):
        self.detached_calls += 1

    def stop(self):
        self.stopped = True


class FakeUndetachableIcon(FakeIcon):
    detachable = False


class FakePystray:
    def __init__(self, icon_cls=FakeIcon):
        self.Icon = icon_cls

    class Menu:
        def __init__(self, *items):
            self.items = items

    class MenuItem:
        def __init__(self, text, action, default=False):
            self.text = text
            self.action = action
            self.default = default


@pytest.fixture
def tray_env(monkeypatch):
    """Make the tray 'available' with a fake pystray; return the fake module."""
    fake = FakePystray()
    monkeypatch.setattr(tray_module, "has_display", lambda: True)
    monkeypatch.setattr(tray_module, "try_import", lambda name: fake)
    monkeypatch.setattr(tray_module, "_make_icon_image", lambda: object())
    return fake


def test_tray_unavailable_without_pystray(monkeypatch):
    monkeypatch.setattr(tray_module, "has_display", lambda: True)
    monkeypatch.setattr(tray_module, "try_import", lambda name: None)
    tray = TrayIcon()
    assert tray.available() is False
    assert tray.start() is False


def test_tray_runs_in_thread_off_macos(tray_env, monkeypatch):
    monkeypatch.setattr(tray_module, "is_macos", lambda: False)
    tray = TrayIcon()
    assert tray.start() is True
    tray._thread.join(timeout=2.0)
    assert tray._icon.run_calls == 1
    assert tray._icon.detached_calls == 0


def test_tray_macos_uses_run_detached(tray_env, monkeypatch):
    monkeypatch.setattr(tray_module, "is_macos", lambda: True)
    tray = TrayIcon()
    assert tray.start() is True
    assert tray._icon.detached_calls == 1
    assert tray._icon.run_calls == 0
    assert tray._thread is None  # never run() in a secondary thread on macOS


def test_tray_macos_skips_without_run_detached(tray_env, monkeypatch):
    monkeypatch.setattr(tray_module, "is_macos", lambda: True)
    tray_env.Icon = FakeUndetachableIcon
    tray = TrayIcon()
    assert tray.start() is False
    assert tray._icon is None  # cleared so stop() is a no-op


def test_tray_macos_skips_when_run_detached_raises(tray_env, monkeypatch):
    monkeypatch.setattr(tray_module, "is_macos", lambda: True)

    class ExplodingIcon(FakeIcon):
        def run_detached(self):
            raise NotImplementedError("no NSApplication")

    tray_env.Icon = ExplodingIcon
    tray = TrayIcon()
    assert tray.start() is False
    assert tray._icon is None
