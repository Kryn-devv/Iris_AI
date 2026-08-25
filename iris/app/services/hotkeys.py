"""Global hotkey service (optional, needs pynput and a display).

Two default bindings:

* ``ctrl+alt+space`` — summon: open/focus the IRIS window in the browser.
* ``ctrl+alt+i``     — push-to-talk signal: publishes a bus event the UI turns
  into a mic toggle when it's open.

Headless machines or missing pynput simply skip hotkeys.
"""

from __future__ import annotations

import threading
import webbrowser
from typing import Any, Optional

from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import has_display, try_import

logger = get_logger("services.hotkeys")


def _to_pynput_combo(combo: str) -> str:
    """Convert "ctrl+alt+space" to pynput's "<ctrl>+<alt>+<space>" syntax."""
    parts = []
    for raw in combo.lower().split("+"):
        key = raw.strip()
        if not key:
            continue
        if len(key) == 1 and key.isalnum():
            parts.append(key)
        else:
            parts.append(f"<{'cmd' if key in ('win', 'super', 'command') else key}>")
    return "+".join(parts)


class HotkeyService:
    """Registers global hotkeys via pynput when possible."""

    def __init__(self) -> None:
        self._listener: Any = None
        self.running = False

    def available(self) -> bool:
        return has_display() and try_import("pynput") is not None

    def start(self) -> bool:
        if self.running or not settings.HOTKEYS_ENABLED:
            return False
        if not self.available():
            logger.info("Global hotkeys unavailable (needs a display + pynput).")
            return False

        pynput = try_import("pynput")
        if pynput is None:
            return False

        def summon() -> None:
            logger.info("Summon hotkey pressed.")
            default_event_bus.publish(Topics.UI_STATE, {"action": "summon"})
            webbrowser.open(settings.base_url)

        def push_to_talk() -> None:
            default_event_bus.publish(Topics.UI_STATE, {"action": "push_to_talk"})

        try:
            bindings = {
                _to_pynput_combo(settings.SUMMON_HOTKEY): summon,
                _to_pynput_combo(settings.PUSH_TO_TALK_HOTKEY): push_to_talk,
            }
            self._listener = pynput.keyboard.GlobalHotKeys(bindings)
            self._listener.daemon = True
            self._listener.start()
            self.running = True
            logger.info("Global hotkeys active: %s (summon), %s (push-to-talk)",
                        settings.SUMMON_HOTKEY, settings.PUSH_TO_TALK_HOTKEY)
            return True
        except Exception as exc:  # noqa: BLE001 - hotkeys are cosmetic
            logger.warning("Hotkey registration failed: %s", exc)
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
        self.running = False


default_hotkey_service = HotkeyService()
