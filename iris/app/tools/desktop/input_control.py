"""Keyboard, mouse and screen input tools built on ``pyautogui``.

These tools give IRIS direct control of the local desktop: typing text into
the focused window, pressing hotkey combinations, clicking, moving the
pointer, scrolling, and reporting the screen geometry. They all declare the
``gui_input`` capability (satisfied by the ``pyautogui`` package), so on a
machine without it — or without a graphical session — the base class reports
a clear "capability unavailable" result instead of crashing.

Safety notes:

* PyAutoGUI's fail-safe is always (re-)enabled before any action, so the user
  can abort runaway automation by slamming the mouse into the top-left corner
  of the screen.
* Every key token in a hotkey request is validated against a static allowlist
  (:data:`KEY_ALLOWLIST`) with a rich alias table (:data:`KEY_ALIASES`), so a
  model can never smuggle arbitrary strings into ``pyautogui.hotkey``.
* All blocking pyautogui calls run in a worker thread via ``to_thread``.
"""

from __future__ import annotations

import string
from typing import Any, Callable, Dict

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import is_macos, try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.desktop.input")


# =============================================================================
# Key allowlist and normalization
# =============================================================================

#: Named (multi-character) keys accepted by :func:`parse_key_combo`. These
#: mirror pyautogui's KEYBOARD_KEYS names so validated tokens can be passed
#: straight to ``pyautogui.press``/``pyautogui.hotkey``.
_NAMED_KEYS: tuple[str, ...] = (
    "enter", "esc", "tab", "space", "backspace", "delete",
    "home", "end", "pageup", "pagedown",
    "up", "down", "left", "right",
    "ctrl", "ctrlleft", "ctrlright",
    "alt", "altleft", "altright",
    "shift", "shiftleft", "shiftright",
    "win", "winleft", "winright", "command",
    "volumeup", "volumedown", "volumemute",
    "playpause", "nexttrack", "prevtrack",
    "printscreen", "insert",
    "capslock", "numlock", "scrolllock",
    "pause", "apps", "fn",
)

#: Every key token a hotkey string may contain, after normalization:
#: letters, digits, f1–f24, printable punctuation, and the named keys above.
KEY_ALLOWLIST: frozenset[str] = (
    frozenset(string.ascii_lowercase)
    | frozenset(string.digits)
    | frozenset(f"f{i}" for i in range(1, 25))
    | frozenset(string.punctuation)
    | frozenset(_NAMED_KEYS)
)

#: Spelling variants people (and models) actually produce, mapped onto the
#: canonical pyautogui names in :data:`KEY_ALLOWLIST`. Lookups happen after
#: lowercasing and stripping spaces/underscores/hyphens, so "Page Up",
#: "page_up" and "PageUp" all resolve to "pageup" before this table applies.
KEY_ALIASES: Dict[str, str] = {
    # editing / whitespace
    "return": "enter", "cr": "enter", "newline": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "spacebar": "space",
    "bksp": "backspace", "bs": "backspace",
    # navigation
    "pgup": "pageup", "pgdn": "pagedown", "pgdown": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "uparrow": "up", "downarrow": "down", "leftarrow": "left", "rightarrow": "right",
    # modifiers
    "control": "ctrl",
    "option": "alt", "opt": "alt",
    "cmd": "command",
    "windows": "win", "super": "win", "meta": "win",
    # system / media
    "prtsc": "printscreen", "prtscr": "printscreen", "printscrn": "printscreen",
    "sysrq": "printscreen",
    "menu": "apps", "contextmenu": "apps",
    "caps": "capslock",
    "mute": "volumemute", "volup": "volumeup", "voldown": "volumedown",
    "play": "playpause", "mediaplaypause": "playpause",
    "next": "nexttrack", "nextsong": "nexttrack", "medianext": "nexttrack",
    "prev": "prevtrack", "previous": "prevtrack", "previoustrack": "prevtrack",
    "prevsong": "prevtrack", "mediaprevious": "prevtrack",
    # spelled-out punctuation
    "plus": "+", "minus": "-", "dash": "-", "hyphen": "-",
    "equals": "=", "equal": "=",
    "comma": ",", "period": ".", "dot": ".",
    "slash": "/", "forwardslash": "/", "backslash": "\\",
    "semicolon": ";", "colon": ":",
    "quote": "'", "apostrophe": "'", "doublequote": "\"",
    "tilde": "~", "grave": "`", "backtick": "`",
    "asterisk": "*", "star": "*",
    "underscore": "_",
    "question": "?", "questionmark": "?",
    "exclamation": "!", "bang": "!",
    "at": "@", "hash": "#", "pound": "#",
    "dollar": "$", "percent": "%", "caret": "^",
    "ampersand": "&", "amp": "&", "pipe": "|",
    "lbracket": "[", "rbracket": "]",
    "lparen": "(", "rparen": ")",
    "lbrace": "{", "rbrace": "}",
    "lessthan": "<", "greaterthan": ">", "less": "<", "greater": ">",
}

_VALID_KEY_EXAMPLES = (
    'letters, digits, f1-f24, punctuation, and named keys such as "enter", "esc", '
    '"tab", "space", "backspace", "delete", "home", "end", "pageup", "pagedown", '
    '"up", "down", "left", "right", "ctrl", "alt", "shift", "win", "command", '
    '"volumeup", "volumedown", "volumemute", "playpause", "nexttrack", "prevtrack", '
    '"printscreen", "insert"'
)


def normalize_key(token: str) -> str:
    """Normalize one key token to its canonical pyautogui name.

    Lowercases, collapses internal spaces/underscores/hyphens for multi-char
    tokens ("Page Up" -> "pageup"), then applies :data:`KEY_ALIASES`
    ("return" -> "enter", "cmd" -> "command"). Single characters pass through
    untouched so punctuation keys like ``-`` and ``_`` survive.
    """
    key = token.strip().lower()
    if len(key) > 1:
        key = key.replace(" ", "").replace("_", "").replace("-", "")
        key = KEY_ALIASES.get(key, key)
    return key


def parse_key_combo(keys: str) -> list[str]:
    """Parse a hotkey string like ``"ctrl+shift+t"`` into validated tokens.

    Splits on ``+`` (a doubled ``++`` denotes the literal plus key, and a
    lone ``"+"`` is the plus key itself), normalizes each token via
    :func:`normalize_key`, and validates every token against
    :data:`KEY_ALLOWLIST`.

    Raises:
        ToolError: when the string is empty or contains unknown key names.
            The message lists the offending tokens and valid examples.
    """
    text = (keys or "").strip()
    if not text:
        raise ToolError(
            "No keys given. Provide a key or combination like 'enter' or 'ctrl+shift+t'.",
            speech="I need to know which keys to press.",
        )

    raw = text.split("+")
    tokens: list[str] = []
    i = 0
    while i < len(raw):
        part = raw[i].strip()
        if part:
            tokens.append(part)
            i += 1
            continue
        # An empty segment comes from a doubled '+' (or a lone '+'): it is
        # the literal plus key. The split produces a *pair* of empty segments
        # for each literal plus at the end of the string, so consume both.
        tokens.append("+")
        i += 1
        if i < len(raw) and not raw[i].strip():
            i += 1

    normalized = [normalize_key(token) for token in tokens]
    unknown = sorted({token for token in normalized if token not in KEY_ALLOWLIST})
    if unknown:
        listed = ", ".join(repr(token) for token in unknown)
        raise ToolError(
            f"Unknown key name(s): {listed}. Valid keys include {_VALID_KEY_EXAMPLES}.",
            speech="I don't recognize one of those key names.",
        )
    return normalized


# =============================================================================
# Shared helpers
# =============================================================================


def _load_pyautogui() -> Any:
    """Import pyautogui, keeping its fail-safe on, or raise a clean ToolError.

    ``try_import`` also returns ``None`` when pyautogui is installed but
    cannot initialize (e.g. no ``DISPLAY`` on Linux), so this covers both
    "not installed" and "headless session".
    """
    pag = try_import("pyautogui")
    if pag is None:
        hint = (
            "pyautogui is not usable on this machine. Install it with "
            "'pip install pyautogui' and make sure a graphical session "
            "(DISPLAY/WAYLAND_DISPLAY) is running."
        )
        if is_macos():
            hint += (
                " On macOS, also grant Accessibility permission to the app "
                "running IRIS (System Settings > Privacy & Security > "
                "Accessibility) — without it key and mouse events are "
                "silently dropped."
            )
        raise ToolError(
            hint,
            speech="I can't control the keyboard or mouse on this machine.",
        )
    # Never disable the abort gesture, whatever a previous caller did.
    pag.FAILSAFE = True
    return pag


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_int(value: Any, name: str) -> int:
    """Coerce a model-supplied argument to int with a friendly error."""
    if isinstance(value, bool):
        raise ToolError(f"'{name}' must be a whole number, got {value!r}.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' must be a whole number, got {value!r}.") from None


def _as_float(value: Any, name: str, default: float) -> float:
    """Coerce a model-supplied argument to float with a friendly error."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ToolError(f"'{name}' must be a number, got {value!r}.")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' must be a number, got {value!r}.") from None


# =============================================================================
# Tools
# =============================================================================


class TypeTextTool(BaseTool):
    """Type literal text on the keyboard into the focused window."""

    name = "type_text"
    description = "Types text on the keyboard into whatever window currently has focus."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("keyboard_type", "write_text")
    required_capabilities = ("gui_input",)
    mutating = True
    examples = (
        ToolExample(utterance="type hello world", arguments={"text": "hello world"}),
        ToolExample(
            utterance="type my search query and press enter",
            arguments={"text": "weather in Pune", "press_enter": True},
        ),
        ToolExample(
            utterance="slowly type the password field placeholder",
            arguments={"text": "correct horse battery staple", "interval": 0.1},
        ),
    )
    input_schema = ToolParameterSchema(
        properties={
            "text": {
                "type": "string",
                "description": "The exact text to type into the focused window.",
            },
            "interval": {
                "type": "number",
                "description": "Seconds to wait between keystrokes (0-1, default 0.02).",
            },
            "press_enter": {
                "type": "boolean",
                "description": "Press Enter after typing the text (default false).",
            },
        },
        required=["text"],
    )

    async def _run(
        self,
        text: Any = None,
        interval: Any = 0.02,
        press_enter: Any = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not isinstance(text, str) or text == "":
            raise ToolError(
                "Provide the text to type ('text' must be a non-empty string).",
                speech="I need the text you want me to type.",
            )
        delay = _clamp(_as_float(interval, "interval", 0.02), 0.0, 1.0)
        press_enter = bool(press_enter)
        pag = _load_pyautogui()

        def _type() -> None:
            writer: Callable[..., Any] = getattr(pag, "write", None) or pag.typewrite
            writer(text, interval=delay)
            if press_enter:
                pag.press("enter")

        await self.to_thread(_type)

        count = len(text)
        preview = text if count <= 60 else text[:57] + "..."
        speech = f"Typed {count} character{'s' if count != 1 else ''}"
        speech += " and pressed enter." if press_enter else "."
        return {
            "typed_characters": count,
            "interval": delay,
            "pressed_enter": press_enter,
            "preview": preview,
            "speech": speech,
            "display": f"Typed: {preview}",
        }


class PressKeysTool(BaseTool):
    """Press a single key or a hotkey combination."""

    name = "press_keys"
    description = "Presses a key or hotkey combination, e.g. 'enter' or 'ctrl+shift+t'."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("hotkey", "shortcut", "key_combo")
    required_capabilities = ("gui_input",)
    mutating = True
    examples = (
        ToolExample(utterance="press enter", arguments={"keys": "enter"}),
        ToolExample(utterance="reopen the closed tab", arguments={"keys": "ctrl+shift+t"}),
        ToolExample(utterance="press tab three times", arguments={"keys": "tab", "presses": 3}),
        ToolExample(utterance="turn the volume up", arguments={"keys": "volumeup", "presses": 5}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "keys": {
                "type": "string",
                "description": (
                    "Key or '+'-joined combination, e.g. 'enter', 'ctrl+c', 'ctrl+shift+t', "
                    "'win+d', 'command+space'."
                ),
            },
            "presses": {
                "type": "integer",
                "description": "How many times to press the key/combination (1-50, default 1).",
            },
        },
        required=["keys"],
    )

    #: Bounds for the repeat count — enough for volume ramps, too few to spam.
    MAX_PRESSES = 50

    async def _run(self, keys: Any = None, presses: Any = 1, **kwargs: Any) -> Dict[str, Any]:
        if not isinstance(keys, str) or not keys.strip():
            raise ToolError(
                "Provide the keys to press, e.g. 'enter' or 'ctrl+shift+t'.",
                speech="I need to know which keys to press.",
            )
        tokens = parse_key_combo(keys)
        count = _as_int(presses, "presses")
        if not 1 <= count <= self.MAX_PRESSES:
            raise ToolError(
                f"'presses' must be between 1 and {self.MAX_PRESSES}, got {count}.",
                speech="That's not a sensible number of key presses.",
            )
        pag = _load_pyautogui()

        def _press() -> None:
            if len(tokens) == 1:
                pag.press(tokens[0], presses=count, interval=0.05)
            else:
                for _ in range(count):
                    pag.hotkey(*tokens)

        await self.to_thread(_press)

        combo = "+".join(tokens)
        speech = f"Pressed {combo}." if count == 1 else f"Pressed {combo} {count} times."
        return {"keys": tokens, "combo": combo, "presses": count, "speech": speech}


class MouseClickTool(BaseTool):
    """Click the mouse, optionally at explicit screen coordinates."""

    name = "mouse_click"
    description = "Clicks the mouse (left/right/middle/double) at given coordinates or the current position."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("click", "mouse_press", "double_click")
    required_capabilities = ("gui_input",)
    mutating = True
    examples = (
        ToolExample(utterance="click", arguments={}),
        ToolExample(utterance="right click at 640 360", arguments={"x": 640, "y": 360, "button": "right"}),
        ToolExample(utterance="double click the icon at 100 200", arguments={"x": 100, "y": 200, "button": "double"}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "x": {
                "type": "integer",
                "description": "Screen X coordinate. Omit x and y to click at the current pointer position.",
            },
            "y": {
                "type": "integer",
                "description": "Screen Y coordinate. Omit x and y to click at the current pointer position.",
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle", "double"],
                "description": "Which click to perform (default 'left'; 'double' is a left double-click).",
            },
        },
        required=[],
    )

    _BUTTONS = ("left", "right", "middle", "double")
    _BUTTON_ALIASES = {
        "leftclick": "left", "rightclick": "right", "middleclick": "middle",
        "doubleclick": "double", "double_click": "double", "double-click": "double",
        "primary": "left", "secondary": "right",
    }

    async def _run(
        self,
        x: Any = None,
        y: Any = None,
        button: Any = "left",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        choice = str(button or "left").strip().lower()
        choice = self._BUTTON_ALIASES.get(choice, choice)
        if choice not in self._BUTTONS:
            raise ToolError(
                f"Unknown button {button!r}. Use one of: {', '.join(self._BUTTONS)}.",
                speech="I don't know that mouse button.",
            )
        if (x is None) != (y is None):
            raise ToolError(
                "Provide both 'x' and 'y', or neither to click at the current pointer position.",
                speech="I need both coordinates, or neither.",
            )
        cx = _as_int(x, "x") if x is not None else None
        cy = _as_int(y, "y") if y is not None else None
        pag = _load_pyautogui()

        def _click() -> tuple[int, int]:
            if cx is not None and cy is not None:
                width, height = pag.size()
                if not (0 <= cx < width and 0 <= cy < height):
                    raise ToolError(
                        f"({cx}, {cy}) is outside the screen (resolution {width}x{height}).",
                        speech="Those coordinates are off the screen.",
                    )
            if choice == "double":
                pag.doubleClick(x=cx, y=cy)
            else:
                pag.click(x=cx, y=cy, button=choice)
            px, py = pag.position()
            return int(px), int(py)

        fx, fy = await self.to_thread(_click)

        where = f"at ({fx}, {fy})"
        action = "Double-clicked" if choice == "double" else f"{choice.capitalize()}-clicked"
        return {"button": choice, "x": fx, "y": fy, "speech": f"{action} {where}."}


class MouseMoveTool(BaseTool):
    """Move the mouse pointer to absolute screen coordinates."""

    name = "mouse_move"
    description = "Moves the mouse pointer to the given screen coordinates."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("move_mouse", "move_cursor", "mouse_to")
    required_capabilities = ("gui_input",)
    mutating = True
    examples = (
        ToolExample(utterance="move the mouse to 960 540", arguments={"x": 960, "y": 540}),
        ToolExample(
            utterance="glide the cursor to the top left corner",
            arguments={"x": 5, "y": 5, "duration": 1.0},
        ),
    )
    input_schema = ToolParameterSchema(
        properties={
            "x": {"type": "integer", "description": "Target screen X coordinate."},
            "y": {"type": "integer", "description": "Target screen Y coordinate."},
            "duration": {
                "type": "number",
                "description": "Seconds the glide should take (0-5, default 0.2; 0 jumps instantly).",
            },
        },
        required=["x", "y"],
    )

    async def _run(
        self,
        x: Any = None,
        y: Any = None,
        duration: Any = 0.2,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if x is None or y is None:
            raise ToolError(
                "Both 'x' and 'y' coordinates are required to move the mouse.",
                speech="I need both coordinates to move the mouse.",
            )
        cx = _as_int(x, "x")
        cy = _as_int(y, "y")
        glide = _clamp(_as_float(duration, "duration", 0.2), 0.0, 5.0)
        pag = _load_pyautogui()

        def _move() -> None:
            width, height = pag.size()
            if not (0 <= cx < width and 0 <= cy < height):
                raise ToolError(
                    f"({cx}, {cy}) is outside the screen (resolution {width}x{height}).",
                    speech="Those coordinates are off the screen.",
                )
            pag.moveTo(cx, cy, duration=glide)

        await self.to_thread(_move)
        return {
            "x": cx,
            "y": cy,
            "duration": glide,
            "speech": f"Moved the mouse to ({cx}, {cy}).",
        }


class ScrollTool(BaseTool):
    """Scroll the mouse wheel up or down."""

    name = "scroll"
    description = "Scrolls the mouse wheel up or down by a number of clicks."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.DESKTOP
    aliases = ("mouse_scroll", "scroll_page", "wheel")
    required_capabilities = ("gui_input",)
    mutating = True
    examples = (
        ToolExample(utterance="scroll down", arguments={"direction": "down"}),
        ToolExample(utterance="scroll up a lot", arguments={"direction": "up", "amount": 15}),
    )
    input_schema = ToolParameterSchema(
        properties={
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "description": "Which way to scroll.",
            },
            "amount": {
                "type": "integer",
                "description": "Number of wheel clicks (1-100, default 5).",
            },
        },
        required=["direction"],
    )

    MAX_AMOUNT = 100
    _DIRECTION_ALIASES = {
        "up": "up", "upward": "up", "upwards": "up", "top": "up",
        "down": "down", "downward": "down", "downwards": "down", "bottom": "down",
    }

    async def _run(self, direction: Any = None, amount: Any = 5, **kwargs: Any) -> Dict[str, Any]:
        heading = self._DIRECTION_ALIASES.get(str(direction or "").strip().lower())
        if heading is None:
            raise ToolError(
                f"Unknown scroll direction {direction!r}. Use 'up' or 'down'.",
                speech="I can only scroll up or down.",
            )
        clicks = _as_int(amount, "amount")
        if not 1 <= clicks <= self.MAX_AMOUNT:
            raise ToolError(
                f"'amount' must be between 1 and {self.MAX_AMOUNT}, got {clicks}.",
                speech="That's not a sensible scroll amount.",
            )
        pag = _load_pyautogui()

        signed = clicks if heading == "up" else -clicks
        await self.to_thread(pag.scroll, signed)

        return {
            "direction": heading,
            "amount": clicks,
            "speech": f"Scrolled {heading} {clicks} click{'s' if clicks != 1 else ''}.",
        }


class ScreenSizeTool(BaseTool):
    """Report the screen resolution and current mouse position."""

    name = "screen_size"
    description = "Reports the screen resolution and the current mouse pointer position."
    permission_level = PermissionLevel.READ
    category = ToolCategory.DESKTOP
    aliases = ("screen_resolution", "get_screen_size", "mouse_position")
    required_capabilities = ("gui_input",)
    mutating = False
    examples = (
        ToolExample(utterance="what's my screen resolution", arguments={}),
        ToolExample(utterance="where is the mouse right now", arguments={}),
    )
    input_schema = ToolParameterSchema(properties={}, required=[])

    async def _run(self, **kwargs: Any) -> Dict[str, Any]:
        pag = _load_pyautogui()

        def _probe() -> tuple[int, int, int, int]:
            width, height = pag.size()
            px, py = pag.position()
            return int(width), int(height), int(px), int(py)

        width, height, px, py = await self.to_thread(_probe)
        return {
            "width": width,
            "height": height,
            "mouse_x": px,
            "mouse_y": py,
            "speech": f"The screen is {width} by {height} pixels; the mouse is at ({px}, {py}).",
        }


def get_tools() -> list[BaseTool]:
    return [
        TypeTextTool(),
        PressKeysTool(),
        MouseClickTool(),
        MouseMoveTool(),
        ScrollTool(),
        ScreenSizeTool(),
    ]
