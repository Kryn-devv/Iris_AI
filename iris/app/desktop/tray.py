"""System tray icon for IRIS (optional, needs pystray + pillow).

The tray gives IRIS a desktop presence: open the UI, toggle voice, quit.
When pystray or a display is unavailable this module simply does nothing.
"""

from __future__ import annotations

import threading
import webbrowser
from typing import Any, Callable, Optional

from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import has_display, is_macos, try_import

logger = get_logger("desktop.tray")


def _make_icon_image():
    """Draw the IRIS orb: a glowing ring on transparent background."""
    from PIL import Image, ImageDraw  # type: ignore[import-not-found]

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accent = (94, 234, 212, 255)
    dim = (94, 234, 212, 90)
    draw.ellipse([6, 6, size - 6, size - 6], outline=dim, width=3)
    draw.ellipse([12, 12, size - 12, size - 12], outline=accent, width=4)
    draw.ellipse([24, 24, size - 24, size - 24], fill=accent)
    return image


class TrayIcon:
    """Wraps pystray so the rest of the app never imports it directly."""

    def __init__(self, on_quit: Optional[Callable[[], None]] = None):
        self.on_quit = on_quit
        self._icon: Any = None
        self._thread: Optional[threading.Thread] = None

    def available(self) -> bool:
        return has_display() and try_import("pystray") is not None and try_import("PIL") is not None

    def start(self) -> bool:
        """Start the tray icon in a daemon thread. Returns success."""
        if not self.available():
            logger.info("Tray unavailable (needs a display plus pystray + pillow).")
            return False

        pystray = try_import("pystray")
        if pystray is None:
            return False

        def open_ui(icon: Any = None, item: Any = None) -> None:
            webbrowser.open(settings.base_url)

        def quit_app(icon: Any = None, item: Any = None) -> None:
            try:
                if self._icon is not None:
                    self._icon.stop()
            finally:
                if self.on_quit:
                    self.on_quit()

        try:
            menu = pystray.Menu(
                pystray.MenuItem("Open IRIS", open_ui, default=True),
                pystray.MenuItem("Quit IRIS", quit_app),
            )
            self._icon = pystray.Icon("iris", _make_icon_image(), "IRIS Assistant", menu)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tray icon creation failed: %s", exc)
            return False

        if is_macos():
            # pystray's darwin backend must own the main thread's event loop;
            # run() in a secondary thread aborts the process. run_detached()
            # (available when pyobjc is installed) is the only safe option.
            run_detached = getattr(self._icon, "run_detached", None)
            if not callable(run_detached):
                logger.info(
                    "Tray skipped on macOS: pystray cannot run() in a background "
                    "thread and this pystray build has no run_detached()."
                )
                self._icon = None
                return False
            try:
                run_detached()
            except Exception as exc:  # noqa: BLE001 - tray is always optional
                logger.info("Tray skipped on macOS: run_detached() failed: %s", exc)
                self._icon = None
                return False
            logger.info("Tray icon started (detached).")
            return True

        self._thread = threading.Thread(target=self._icon.run, name="iris-tray", daemon=True)
        self._thread.start()
        logger.info("Tray icon started.")
        return True

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self._icon = None
