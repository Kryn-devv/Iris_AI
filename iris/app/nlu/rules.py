"""The intent rule catalogue for IRIS's deterministic command engine.

Rules are ordered: the first pattern that matches (and whose slot builder
accepts the match) wins. Specific, high-signal phrasings come first; broad
catch-alls come last. Every rule maps to a registered tool name and produces
that tool's arguments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Match, Optional, Pattern

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_FILLER_PREFIX = re.compile(
    r"^(?:hey\s+|ok\s+|okay\s+)?(?:iris\s*[,:]?\s+)?"
    r"(?:please\s+|kindly\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|will\s+you\s+)?"
    r"(?:please\s+)?",
    re.IGNORECASE,
)
_TRAILING_POLITENESS = re.compile(r"\s*(?:please|for me|thanks|thank you|now)\s*[.!?]*$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize_command(text: str) -> str:
    """Lowercase, strip wake words, politeness and extra whitespace."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cleaned = _FILLER_PREFIX.sub("", cleaned)
    cleaned = _TRAILING_POLITENESS.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned.lower().rstrip(".!?").strip()


# ---------------------------------------------------------------------------
# Rule machinery
# ---------------------------------------------------------------------------

Builder = Callable[[Match[str], str], Optional[Dict[str, Any]]]


@dataclass
class Rule:
    """One intent pattern mapped to a tool."""

    name: str
    intent: str
    tool: str
    pattern: Pattern[str]
    builder: Optional[Builder] = None
    static_args: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.95
    needs_generation: bool = False

    def build(self, m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
        if self.builder is not None:
            built = self.builder(m, cleaned)
            if built is None:
                return None
            merged = dict(self.static_args)
            merged.update(built)
            return merged
        args = dict(self.static_args)
        for key, value in (m.groupdict() or {}).items():
            if value is not None:
                args[key] = value.strip()
        return args


def _rx(pattern: str) -> Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Shared vocab
# ---------------------------------------------------------------------------

#: Sites the "open X" rule treats as websites rather than applications.
KNOWN_SITES = frozenset(
    {
        "youtube", "google", "gmail", "github", "whatsapp", "twitter", "x",
        "instagram", "facebook", "netflix", "prime video", "chatgpt", "claude",
        "maps", "google maps", "translate", "google translate", "drive",
        "google drive", "docs", "google docs", "sheets", "slides", "reddit",
        "stackoverflow", "stack overflow", "linkedin", "amazon", "flipkart",
        "wikipedia", "twitch", "pinterest", "canva", "figma", "notion",
        "outlook", "calendar", "google calendar", "hotstar", "spotify web",
    }
)

_DOMAIN_RX = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(/\S*)?$", re.IGNORECASE)

#: Folder words the "open X" rule routes to the file manager.
from iris.app.core import paths as _paths

KNOWN_FOLDERS = {
    "downloads": "~/Downloads",
    "documents": "~/Documents",
    "desktop": "~/Desktop",
    "pictures": "~/Pictures",
    "photos": "~/Pictures",
    "music": "~/Music",
    "videos": "~/Videos",
    "home": "~",
    "home folder": "~",
    # Workspace paths honour IRIS_WORKSPACE_DIR instead of hardcoding ~/Iris.
    "iris": str(_paths.workspace_dir()),
    "iris folder": str(_paths.workspace_dir()),
    "outputs": str(_paths.outputs_dir()),
    "my outputs": str(_paths.outputs_dir()),
    "projects": str(_paths.projects_dir()),
    "screenshots": str(_paths.screenshots_dir()),
}

#: File extensions that mark an "open X" target as a file, not a website.
_FILE_EXTENSIONS = (
    ".pdf", ".txt", ".md", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx",
    ".ppt", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mkv", ".mov",
    ".mp3", ".wav", ".m4a", ".zip", ".rar", ".7z", ".py", ".js", ".html",
    ".json", ".ino", ".exe", ".msi", ".apk", ".iso", ".svg", ".rtf", ".odt",
)

_NUMBER_WORDS = {
    "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "sixty": 60,
    "ninety": 90, "half": 0.5,
}


def parse_number(token: str) -> Optional[float]:
    """Parse '10', 'ten' or 'half' into a number."""
    token = token.strip().lower()
    if token in _NUMBER_WORDS:
        return float(_NUMBER_WORDS[token])
    try:
        return float(token)
    except ValueError:
        return None


def parse_duration_seconds(amount: str, unit: str) -> Optional[int]:
    """Convert an (amount, unit) pair into seconds."""
    value = parse_number(amount)
    if value is None or value <= 0:
        return None
    unit = unit.lower()
    if unit.startswith("sec"):
        return int(value)
    if unit.startswith("min"):
        return int(value * 60)
    if unit.startswith(("hour", "hr")):
        return int(value * 3600)
    return None


# ---------------------------------------------------------------------------
# Slot builders
# ---------------------------------------------------------------------------

def _build_open_target(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    """Decide file vs folder vs website vs application for a bare 'open X'."""
    target = (m.group("target") or "").strip().rstrip(".")
    if not target:
        return None
    lowered = target.lower()

    # A real file name ("report.pdf") or path ("downloads/report.pdf", "~/x")
    # must never be treated as a web domain.
    if lowered.endswith(_FILE_EXTENSIONS):
        if "/" in target or "\\" in target or target.startswith("~"):
            return {"__tool__": "open_path", "path": target}
        return {"__tool__": "find_and_open", "name": Path(target).stem, "kind": "file"}
    if "/" in target or "\\" in target or target.startswith("~"):
        return {"__tool__": "open_path", "path": target}

    if lowered in KNOWN_SITES or _DOMAIN_RX.match(lowered) or lowered.startswith(("http://", "https://")):
        return {"__tool__": "open_website", "site": target}

    # Known folders, tolerating "my X", "X folder" and "X directory".
    for candidate in (
        lowered,
        lowered.removeprefix("my ").strip(),
        lowered.removesuffix(" folder").strip(),
        lowered.removesuffix(" directory").strip(),
        lowered.removeprefix("my ").removesuffix(" folder").strip(),
    ):
        folder = KNOWN_FOLDERS.get(candidate)
        if folder:
            return {"__tool__": "open_path", "path": folder}
    return {"__tool__": "open_app", "app": target}


#: Words after "turn on/off" that are NOT smart devices — those phrasings
#: belong to other tools or to the agent, never to device_switch.
_NON_DEVICE_WORDS = frozenset({
    "volume", "sound", "audio", "music", "screen", "display", "monitor",
    "wifi", "wi-fi", "bluetooth", "mic", "microphone", "camera", "pc",
    "computer", "laptop", "notifications", "dark mode", "it", "that",
    "the tv show", "captions",
})


def _build_device_switch(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    """'turn on the kitchen light' -> device_switch, skipping non-device nouns."""
    device = (m.group("dev") or "").strip().rstrip(".")
    state = (m.group("state") or "").strip().lower()
    if not device or len(device) < 2 or state not in ("on", "off"):
        return None
    lowered = device.lower()
    if lowered in _NON_DEVICE_WORDS or any(w in _NON_DEVICE_WORDS for w in (lowered.split()[-1],)):
        return None
    return {"device": device, "state": state}


def _build_device_hinglish(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    """'light chalu karo' / 'fan band kar do' -> device_switch."""
    device = (m.group("dev") or "").strip()
    verb = (m.group("verb") or "").strip().lower()
    if not device or device.lower() in _NON_DEVICE_WORDS:
        return None
    state = "off" if verb in ("band", "bandh") else "on"
    return {"device": device, "state": state}


_SERVO_OPEN_WORDS = ("open", "kholo", "khol", "khol do", "utha do")


def _build_servo_position(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    """A curtain is not an on/off appliance, so 'open' has to become an angle.

    Halfway is a real request and the only one that needs a third position;
    everything else is one end of the travel or the other.
    """
    verb = (m.group("verb") or "").strip().lower()
    # "half" can land either side of the noun — "open half the curtain",
    # "open the curtain halfway" — so read it off the cleaned text rather
    # than carrying three optional groups through the pattern.
    if re.search(r"\bhalf(?:way)?\b", cleaned):
        return {"position": "half"}
    if verb.startswith(_SERVO_OPEN_WORDS):
        return {"position": "open"}
    return {"position": "close"}


def _build_servo_angle(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    angle = int(m.group("angle"))
    if angle > 180:
        return None            # let the LLM explain it rather than clamp silently
    return {"angle": angle}


def _build_motor(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    action = (m.group("action") or "").strip().lower()
    aliases = {"back": "backward", "backwards": "backward", "ahead": "forward", "straight": "forward",
               "ruko": "stop", "chalo": "forward", "aage": "forward", "peeche": "backward"}
    action = aliases.get(action, action)
    if action not in ("forward", "backward", "left", "right", "stop"):
        return None
    args: Dict[str, Any] = {"action": action}
    speed = m.groupdict().get("speed")
    if speed:
        args["speed"] = max(0, min(255, int(speed)))
    return args


#: Spoken words -> the firmware's emotion names. Hindi/Hinglish included
#: because that is how this assistant gets talked to.
_FACE_WORDS = {
    "happy": "happy", "smile": "happy", "smiley": "happy", "glad": "happy",
    "cheerful": "happy", "khush": "happy",
    "sad": "sad", "upset": "sad", "unhappy": "sad", "udaas": "sad",
    "angry": "angry", "mad": "angry", "cross": "angry", "gussa": "angry",
    "excited": "excited", "hyped": "excited",
    "love": "love", "loving": "love", "heart": "love", "hearts": "love", "pyaar": "love",
    "surprised": "surprised", "shocked": "surprised", "shock": "surprised",
    "hairaan": "surprised",
    "sleepy": "sleepy", "tired": "sleepy", "neend": "sleepy",
    "thinking": "thinking", "thoughtful": "thinking",
    "confused": "confused", "puzzled": "confused",
    "listening": "listening", "attentive": "listening",
    "suspicious": "suspicious", "sus": "suspicious",
    "dizzy": "dizzy",
    "neutral": "neutral", "normal": "neutral", "calm": "neutral", "blank": "neutral",
}

_FACE_DIRECTIONS = {
    "left": "left", "right": "right", "up": "up", "down": "down",
    "away": "away", "centre": "centre", "center": "centre",
    "ahead": "centre", "forward": "centre", "straight": "centre",
    "me": "centre", "at me": "centre",
}


def _build_face_emotion(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    word = (m.groupdict().get("mood") or "").strip().lower()
    emotion = _FACE_WORDS.get(word)
    if emotion is None:
        return None
    return {"emotion": emotion}


def _build_face_look(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    word = (m.groupdict().get("dir") or "").strip().lower()
    direction = _FACE_DIRECTIONS.get(word)
    if direction is None:
        return None
    # Aiming the eyes is not a mood change, so keep whatever face is showing.
    return {"emotion": "neutral", "look": direction}


def _build_site_search(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    query = (m.group("query") or "").strip()
    site = (m.group("site") or "").strip()
    if not query or not site:
        return None
    return {"site": site, "query": query}


def _build_timer(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    seconds = parse_duration_seconds(m.group("amount"), m.group("unit"))
    if seconds is None:
        return None
    label = (m.groupdict().get("label") or "").strip() or None
    args: Dict[str, Any] = {"seconds": seconds}
    if label:
        args["label"] = label
    return args


def _build_reminder_in(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    seconds = parse_duration_seconds(m.group("amount"), m.group("unit"))
    if seconds is None:
        return None
    text = (m.groupdict().get("text") or "").strip(" .")
    if not text:
        text = "your reminder"
    return {"text": text, "in_seconds": seconds}


def _build_reminder_at(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    text = (m.groupdict().get("text") or "").strip(" .") or "your reminder"
    hour = int(m.group("hour"))
    minute = int(m.group("minute") or 0)
    meridiem = (m.groupdict().get("meridiem") or "").lower().replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return {"text": text, "at_time": f"{hour:02d}:{minute:02d}"}


def _build_volume_set(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    level = parse_number(m.group("level"))
    if level is None:
        return None
    return {"action": "set", "level": max(0, min(100, int(level)))}


def _build_calc(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    expression = (m.group("expr") or "").strip()
    if not expression or not re.search(r"\d", expression):
        return None
    return {"expression": expression}


def _build_calc_words(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
    """Word-operator math ("25 multiplied by 47", "25 ko 40 se guna karo")."""
    from iris.app.language.normalizer import default_language_normalizer

    expression = default_language_normalizer.normalize_tool_expression(cleaned)
    if not re.fullmatch(r"[\d\s()+\-*/.%]+", expression or ""):
        return None
    if not (re.search(r"\d", expression) and re.search(r"[+\-*/%]", expression)):
        return None
    return {"expression": expression.strip()}


def _passthrough_query(key: str) -> Builder:
    def build(m: Match[str], cleaned: str) -> Optional[Dict[str, Any]]:
        value = (m.group("q") or "").strip(" ?.")
        if not value:
            return None
        return {key: value}

    return build


# ---------------------------------------------------------------------------
# The catalogue (ordered: most specific first)
# ---------------------------------------------------------------------------

RULES: list[Rule] = [
    # ------------------------------------------------- devices / home automation
    Rule(
        name="map_device_command",
        intent="devices",
        tool="map_device_command",
        pattern=_rx(
            r"^(?:map|set)\s+(?:device\s+)?(?P<device>.+?)\s+(?P<command>on|off|toggle|[a-z0-9_-]+)"
            r"\s+(?:command\s+)?(?:to|as|=)\s+(?P<path>/\S+)$"
        ),
        builder=lambda m, c: {
            "device": m.group("device").strip(),
            "command": m.group("command").strip(),
            "path": m.group("path").strip(),
        },
        confidence=0.97,
    ),
    Rule(
        name="device_register",
        intent="devices",
        tool="register_device",
        pattern=_rx(
            r"^(?:add|register|pair|connect)\s+(?:a\s+|new\s+|my\s+)?(?:device|esp32|board|node)\s+"
            r"(?P<name>.+?)\s+(?:at|@|on)\s+(?P<addr>[a-z0-9.:_-]+)"
            r"(?:\s+as\s+(?:a\s+)?(?P<kind>relay|motor|generic))?$"
        ),
        builder=lambda m, c: {
            "name": m.group("name").strip(),
            "address": m.group("addr").strip(),
            **({"kind": m.group("kind")} if m.group("kind") else {}),
        },
        confidence=0.98,
    ),
    Rule(
        name="device_list",
        intent="devices",
        tool="list_devices",
        pattern=_rx(r"^(?:list|show)(?:\s+(?:my|all))?\s+devices$|^what\s+devices\s+do\s+i\s+have$"),
        confidence=0.98,
    ),
    Rule(
        name="device_remove",
        intent="devices",
        tool="remove_device",
        pattern=_rx(r"^(?:remove|forget|delete|unpair)\s+(?:the\s+)?device\s+(?P<name>.+)$"),
        builder=lambda m, c: {"name": m.group("name").strip()},
        confidence=0.98,
    ),
    Rule(
        name="servo_position",
        intent="devices",
        tool="device_servo",
        pattern=_rx(
            r"^(?P<verb>open|close|shut|draw)\s+(?:the\s+|my\s+|half\s+|halfway\s+)*"
            r"(?:curtain|curtains|blind|blinds|shutter|shutters|parda|pardah|latch|valve)"
            r"(?:\s+half(?:way)?)?$"
        ),
        builder=_build_servo_position,
        confidence=0.95,
    ),
    Rule(
        name="servo_position_hinglish",
        intent="devices",
        tool="device_servo",
        pattern=_rx(
            r"^(?:curtain|curtains|blind|blinds|shutter|parda|pardah)\s+"
            r"(?P<verb>kholo|khol\s+do|band\s+karo|band\s+kar\s+do|bandh\s+karo)$"
        ),
        builder=_build_servo_position,
        confidence=0.95,
    ),
    Rule(
        name="servo_angle_set",
        intent="devices",
        tool="device_servo",
        pattern=_rx(
            r"^(?:(?:set|move|turn|put|rotate)\s+)?(?:the\s+|my\s+)?servo\s+"
            r"(?:to\s+|at\s+)?(?P<angle>\d{1,3})(?:\s*(?:degrees?|deg))?$"
        ),
        builder=_build_servo_angle,
        confidence=0.96,
    ),
    Rule(
        name="device_switch_on_off",
        intent="devices",
        tool="device_switch",
        pattern=_rx(r"^(?:turn|switch|power)\s+(?P<state>on|off)\s+(?:the\s+|my\s+)?(?P<dev>.+)$"),
        builder=_build_device_switch,
        confidence=0.95,
    ),
    Rule(
        name="device_switch_suffix",
        intent="devices",
        tool="device_switch",
        pattern=_rx(r"^(?:turn|switch|power)\s+(?:the\s+|my\s+)?(?P<dev>.+?)\s+(?P<state>on|off)$"),
        builder=_build_device_switch,
        confidence=0.94,
    ),
    Rule(
        name="device_switch_hinglish",
        intent="devices",
        tool="device_switch",
        pattern=_rx(r"^(?P<dev>.+?)\s+(?:ko\s+)?(?P<verb>chalu|shuru|on|band|bandh|off)\s+kar(?:o|do|\s+do|\s+dijiye|na)?$"),
        builder=_build_device_hinglish,
        confidence=0.95,
    ),
    Rule(
        name="device_toggle",
        intent="devices",
        tool="device_switch",
        pattern=_rx(r"^toggle\s+(?:the\s+|my\s+)?(?P<dev>.+)$"),
        builder=lambda m, c: (
            {"device": m.group("dev").strip(), "state": "toggle"}
            if m.group("dev").strip().lower() not in _NON_DEVICE_WORDS else None
        ),
        confidence=0.93,
    ),
    Rule(
        name="robot_move",
        intent="devices",
        tool="device_motor",
        pattern=_rx(
            r"^(?:move|drive|make)?\s*(?:the\s+|my\s+)?robot\s*,?\s*"
            r"(?:go\s+|move\s+|turn\s+)?(?P<action>forward|forwards|ahead|straight|back|backward|backwards|left|right|stop|ruko|chalo|aage|peeche)"
            r"(?:\s+at\s+speed\s+(?P<speed>\d{1,3}))?$"
        ),
        builder=_build_motor,
        confidence=0.96,
    ),
    Rule(
        name="robot_stop_short",
        intent="devices",
        tool="device_motor",
        pattern=_rx(r"^(?:stop(?:\s+the)?\s+robot|robot\s+stop|emergency\s+stop)$"),
        static_args={"action": "stop"},
        confidence=0.98,
    ),
    Rule(
        name="sensor_motion_query",
        intent="devices",
        tool="device_sensors",
        pattern=_rx(r"^(?:is\s+there\s+(?:any\s+)?(?:motion|movement|someone|anyone)(?:\s+in\s+the\s+\w+)?|any\s+(?:motion|movement)|koi\s+hai\s+kya|kya\s+koi\s+hai)\??$"),
        static_args={"sensor": "motion"},
        confidence=0.96,
    ),
    Rule(
        name="sensor_gas_query",
        intent="devices",
        tool="device_sensors",
        pattern=_rx(r"^(?:what(?:'s|\s+is)\s+the\s+)?gas\s+level(?:\s+kya\s+hai)?\??$|^(?:any\s+)?gas\s+(?:leak|detected)\??$|^gas\s+check\s+karo$"),
        static_args={"sensor": "gas"},
        confidence=0.96,
    ),
    Rule(
        name="sensor_distance_query",
        intent="devices",
        tool="device_sensors",
        pattern=_rx(r"^(?:how\s+far\s+(?:away\s+)?is\s+(?:the\s+)?(?:object|obstacle|wall|it)|distance\s+(?:check|reading)|kitna\s+door\s+hai)\??$"),
        static_args={"sensor": "distance"},
        confidence=0.95,
    ),
    Rule(
        name="sensor_flame_query",
        intent="devices",
        tool="device_sensors",
        pattern=_rx(
            r"^(?:is\s+there\s+(?:a\s+|any\s+)?(?:fire|flame)|any\s+(?:fire|flame)"
            r"|fire\s+(?:check|detected)|flame\s+(?:check|status)"
            r"|aag\s+(?:lagi\s+hai|hai)(?:\s+kya)?|fire\s+check\s+karo)\??$"
        ),
        static_args={"sensor": "flame"},
        confidence=0.97,
    ),
    Rule(
        name="sensor_temperature_query",
        intent="devices",
        tool="device_sensors",
        pattern=_rx(
            r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:room\s+|current\s+)?temperature"
            r"|how\s+(?:hot|cold|warm)\s+is\s+it(?:\s+in\s+(?:here|the\s+room))?"
            r"|temperature\s+(?:check|reading|batao|kya\s+hai|bataiye)"
            r"|kitna\s+(?:garam|thanda)\s+hai"
            r"|room\s+temperature)\??$"
        ),
        static_args={"sensor": "temperature"},
        confidence=0.96,
    ),
    Rule(
        name="sensor_humidity_query",
        intent="devices",
        tool="device_sensors",
        pattern=_rx(
            r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?humidity"
            r"|how\s+humid\s+is\s+it(?:\s+in\s+(?:here|the\s+room))?"
            r"|humidity\s+(?:check|reading|batao|kya\s+hai)"
            r"|nami\s+kitni\s+hai)\??$"
        ),
        static_args={"sensor": "humidity"},
        confidence=0.96,
    ),
    Rule(
        name="sensor_all_query",
        intent="devices",
        tool="device_sensors",
        pattern=_rx(r"^(?:check\s+(?:the\s+)?sensors?|sensor\s+readings?|read\s+(?:the\s+)?sensors?|what\s+do\s+the\s+sensors\s+(?:say|show))\??$"),
        static_args={"sensor": "all"},
        confidence=0.96,
    ),
    Rule(
        name="face_emotion_set",
        intent="devices",
        tool="face_emotion",
        pattern=_rx(
            r"^(?:(?:show|make|set|be|act|look|do)\s+)?"
            r"(?:me\s+|the\s+|your\s+|a\s+)?"
            r"(?P<mood>[a-z]+)"
            r"(?:\s+(?:eyes|face|expression|mood|ho\s+jao|dikhao))?$"
        ),
        builder=_build_face_emotion,
        confidence=0.93,
    ),
    Rule(
        name="face_wink",
        intent="devices",
        tool="face_emotion",
        pattern=_rx(r"^(?:wink(?:\s+at\s+me)?|aankh\s+maaro|give\s+me\s+a\s+wink)$"),
        static_args={"emotion": "wink", "seconds": 2},
        confidence=0.97,
    ),
    Rule(
        name="face_blink",
        intent="devices",
        tool="face_emotion",
        pattern=_rx(r"^(?:blink(?:\s+(?:your\s+)?eyes)?|palak\s+jhapkao)$"),
        static_args={"emotion": "neutral", "blink": True},
        confidence=0.96,
    ),
    Rule(
        name="face_look_direction",
        intent="devices",
        tool="face_emotion",
        pattern=_rx(
            r"^(?:look|glance|eyes)\s+(?:to\s+(?:the\s+)?|at\s+|towards\s+)?"
            r"(?P<dir>left|right|up|down|away|centre|center|ahead|forward|straight|me|at me)$"
        ),
        builder=_build_face_look,
        confidence=0.94,
    ),
    Rule(
        name="device_status_query",
        intent="devices",
        tool="device_status",
        pattern=_rx(r"^(?:is\s+(?:the\s+|my\s+)?(?P<dev>.+?)\s+(?:on(?:line)?|working|connected|alive)|check\s+(?:the\s+)?devices?|ping\s+(?:the\s+)?(?P<dev2>.+))$"),
        builder=lambda m, c: (
            {"device": (m.group("dev") or m.group("dev2")).strip()}
            if (m.group("dev") or m.group("dev2")) else {}
        ),
        confidence=0.9,
    ),
    Rule(
        name="hinglish_weather",
        intent="web",
        tool="weather",
        pattern=_rx(r"^(?:(?P<q>.+?)\s+(?:ka|mein|me)\s+)?mausam(?:\s+kaisa\s+hai)?$|^weather\s+kaisa\s+hai$"),
        builder=lambda m, c: (
            {"location": m.group("q").strip()} if m.groupdict().get("q") else {}
        ),
        confidence=0.95,
    ),
    Rule(
        name="hinglish_time",
        intent="system",
        tool="time",
        pattern=_rx(r"^(?:kitne\s+baje(?:\s+(?:hain|hai))?|(?:samay|time)\s+kya\s+(?:hua|hai)(?:\s+hai)?|abhi\s+time\s+kya\s+hai)$"),
        confidence=0.96,
    ),
    Rule(
        name="hinglish_screenshot",
        intent="desktop",
        tool="take_screenshot",
        pattern=_rx(r"^screenshot\s+(?:lo|le\s+lo|le|nikalo|kheecho)$"),
        confidence=0.97,
    ),
    Rule(
        name="hinglish_volume",
        intent="media",
        tool="volume",
        pattern=_rx(r"^(?:awaa?z|volume|sound)\s+(?P<verb>badhao|badha\s+do|kam\s+karo|kam\s+kar\s+do|ghatao)$"),
        builder=lambda m, c: {"action": "up" if "badha" in m.group("verb") else "down"},
        confidence=0.96,
    ),
    Rule(
        name="hinglish_play",
        intent="entertainment",
        tool="play_youtube",
        pattern=_rx(r"^(?:(?P<q>.+?)\s+(?:bajao|baja\s+do|sunao|suna\s+do)|(?:gaana|music|song)\s+(?:chalao|bajao|lagao))$"),
        builder=lambda m, c: {"query": (m.groupdict().get("q") or "music").strip()},
        confidence=0.95,
    ),
    Rule(
        name="open_kholo_hinglish",
        intent="apps",
        tool="open_app",
        pattern=_rx(r"^(?P<target>.+?)\s+(?:kholo|khol\s+do|open\s+karo|chalao)$"),
        builder=_build_open_target,
        confidence=0.94,
    ),

    # ------------------------------------------------------------- media/site
    Rule(
        name="play_on_youtube",
        intent="entertainment",
        tool="play_youtube",
        pattern=_rx(r"^play\s+(?P<q>.+?)(?:\s+on\s+youtube)?$"),
        builder=lambda m, c: (
            {"query": m.group("q").strip()}
            if ("youtube" in c or c.startswith("play "))
            and m.group("q").strip()
            and not re.match(r"^(music|song|songs|video|videos|it|that)$", m.group("q").strip())
            else None
        ),
        confidence=0.9,
    ),
    Rule(
        name="search_on_site",
        intent="web",
        tool="open_website",
        pattern=_rx(
            r"^(?:search|look)\s+(?:for\s+)?(?P<query>.+?)\s+on\s+"
            r"(?P<site>youtube|google|amazon|wikipedia|maps|google maps|github|spotify|flipkart|reddit|netflix)$"
        ),
        builder=_build_site_search,
        confidence=0.97,
    ),
    Rule(
        name="youtube_search_alt",
        intent="web",
        tool="open_website",
        pattern=_rx(r"^(?:open\s+)?youtube\s+(?:and\s+)?(?:search|play)\s+(?:for\s+)?(?P<query>.+)$"),
        builder=lambda m, c: {"site": "youtube", "query": m.group("query").strip()},
        confidence=0.97,
    ),

    # ------------------------------------------------------------------ open
    Rule(
        name="open_latest_kind",
        intent="files",
        tool="find_and_open",
        pattern=_rx(
            r"^open\s+(?:my\s+|the\s+)?(?:latest|last|newest|most\s+recent|recent)\s+"
            r"(?P<kind>screenshot|screen\s+shot|photo|picture|image|pic|presentation|ppt|deck|slides|"
            r"document|doc|pdf|spreadsheet|excel|sheet|video|movie|song|audio|download|downloaded\s+file|code|script|file)$"
        ),
        builder=lambda m, c: {"kind": " ".join(m.group("kind").split()), "latest": True},
        confidence=0.97,
    ),
    Rule(
        name="open_that_kind_i_made",
        intent="files",
        tool="find_and_open",
        pattern=_rx(
            r"^open\s+(?:that|the)\s+(?P<kind>screenshot|photo|picture|image|presentation|ppt|deck|"
            r"document|doc|pdf|spreadsheet|sheet|video|song|file)"
            r"(?:\s+(?:i|we|you)\s+(?:made|created|generated|took|saved))?"
            r"(?:\s+(?:yesterday|today|earlier|just\s+now|last\s+\w+))?$"
        ),
        builder=lambda m, c: {"kind": m.group("kind"), "latest": True},
        confidence=0.95,
    ),
    Rule(
        name="find_and_open_by_name",
        intent="files",
        tool="find_and_open",
        pattern=_rx(r"^(?:find\s+and\s+open|open\s+the\s+file)\s+(?P<name>.+)$"),
        builder=lambda m, c: {"name": m.group("name").strip(), "kind": "file"},
        confidence=0.95,
    ),
    Rule(
        name="show_my_folder",
        intent="files",
        tool="open_path",
        pattern=_rx(r"^(?:show(?:\s+me)?|display|browse)\s+(?:my\s+|the\s+)?(?P<target>.+)$"),
        builder=lambda m, c: (
            lambda built: built if built and built.get("__tool__") in ("open_path", "find_and_open") else None
        )(_build_open_target(m, c)),
        confidence=0.92,
    ),
    Rule(
        name="open_target",
        intent="desktop",
        tool="__dynamic__",
        pattern=_rx(r"^(?:open|launch|start|run)\s+(?:up\s+)?(?:the\s+)?(?P<target>[\w .+&/:~\\\\-]{1,80})$"),
        builder=_build_open_target,
        confidence=0.95,
    ),
    Rule(
        name="close_app",
        intent="desktop",
        tool="close_app",
        pattern=_rx(r"^(?:close|quit|exit|kill)\s+(?:the\s+)?(?:app(?:lication)?\s+)?(?P<app>[\w .+-]{2,40})$"),
        builder=lambda m, c: (
            {"app": m.group("app").strip()}
            if m.group("app").strip() not in ("window", "this window", "the window")
            else None
        ),
        confidence=0.85,
    ),

    # ----------------------------------------------------------------- voice
    Rule(
        name="say_text",
        intent="voice",
        tool="speak",
        pattern=_rx(r"^(?:say|speak|read out|read aloud)\s+(?P<text>.+)$"),
        confidence=0.9,
    ),

    # ------------------------------------------------------------ screenshot
    Rule(
        name="screenshot",
        intent="desktop",
        tool="take_screenshot",
        pattern=_rx(r"(?:take|capture|grab)\s+(?:a\s+)?(?:screen\s*shot|screenshot|screen\s+capture)|^screenshot$"),
        confidence=0.98,
    ),

    # ---------------------------------------------------------------- typing
    Rule(
        name="type_text",
        intent="desktop",
        tool="type_text",
        pattern=_rx(r"^type\s+(?:out\s+)?(?P<text>.+)$"),
        confidence=0.92,
    ),
    Rule(
        name="press_keys",
        intent="desktop",
        tool="press_keys",
        pattern=_rx(r"^(?:press|hit)\s+(?P<keys>[\w+ ]{1,40})$"),
        builder=lambda m, c: {"keys": m.group("keys").strip().replace(" and ", "+").replace(" ", "+")
                              if "+" not in m.group("keys") and len(m.group("keys").split()) <= 3
                              else m.group("keys").strip()},
        confidence=0.85,
    ),

    # ---------------------------------------------------------------- volume
    Rule(
        name="volume_set",
        intent="media",
        tool="volume",
        pattern=_rx(r"(?:set\s+)?(?:the\s+)?volume\s+(?:to|at)\s+(?P<level>\d{1,3})\s*(?:percent|%)?"),
        builder=_build_volume_set,
        confidence=0.98,
    ),
    Rule(
        name="volume_up",
        intent="media",
        tool="volume",
        pattern=_rx(r"(?:volume|sound)\s+up|turn\s+(?:it|the\s+volume|the\s+sound)\s+up|increase\s+(?:the\s+)?volume|louder"),
        static_args={"action": "up"},
        confidence=0.97,
    ),
    Rule(
        name="volume_down",
        intent="media",
        tool="volume",
        pattern=_rx(r"(?:volume|sound)\s+down|turn\s+(?:it|the\s+volume|the\s+sound)\s+down|decrease\s+(?:the\s+)?volume|quieter|softer"),
        static_args={"action": "down"},
        confidence=0.97,
    ),
    Rule(
        name="mute",
        intent="media",
        tool="volume",
        pattern=_rx(r"^(?:mute|silence)(?:\s+(?:the\s+)?(?:sound|audio|volume|pc|computer))?$"),
        static_args={"action": "mute"},
        confidence=0.97,
    ),
    Rule(
        name="unmute",
        intent="media",
        tool="volume",
        pattern=_rx(r"^unmute(?:\s+(?:the\s+)?(?:sound|audio|volume))?$"),
        static_args={"action": "unmute"},
        confidence=0.97,
    ),

    # ----------------------------------------------------------- media keys
    Rule(
        name="media_pause_play",
        intent="media",
        tool="media_control",
        pattern=_rx(r"^(?:pause|resume|play)(?:\s+(?:the\s+)?(?:music|song|video|media|playback))?$"),
        static_args={"action": "play_pause"},
        confidence=0.9,
    ),
    Rule(
        name="media_next",
        intent="media",
        tool="media_control",
        pattern=_rx(r"^(?:next|skip)(?:\s+(?:song|track|video))?$|^skip\s+this(?:\s+song)?$"),
        static_args={"action": "next"},
        confidence=0.95,
    ),
    Rule(
        name="media_previous",
        intent="media",
        tool="media_control",
        pattern=_rx(r"^(?:previous|last)\s+(?:song|track)$|^go\s+back(?:\s+a)?(?:\s+(?:song|track))$"),
        static_args={"action": "previous"},
        confidence=0.95,
    ),

    # ------------------------------------------------------------ clipboard
    Rule(
        name="clipboard_read",
        intent="desktop",
        tool="clipboard_read",
        pattern=_rx(r"(?:what(?:'s| is)\s+(?:in|on)\s+(?:my\s+)?clipboard|read\s+(?:the\s+)?clipboard|show\s+clipboard)"),
        confidence=0.97,
    ),
    Rule(
        name="clipboard_write",
        intent="desktop",
        tool="clipboard_write",
        pattern=_rx(r"^copy\s+(?P<text>.+?)\s+to\s+(?:the\s+)?clipboard$"),
        confidence=0.97,
    ),

    # -------------------------------------------------------------- windows
    Rule(
        name="list_windows",
        intent="desktop",
        tool="list_windows",
        pattern=_rx(r"(?:list|show|what)\s+(?:are\s+)?(?:my\s+|the\s+)?(?:open\s+)?windows"),
        confidence=0.95,
    ),
    Rule(
        name="focus_window",
        intent="desktop",
        tool="focus_window",
        pattern=_rx(r"^(?:switch|go)\s+to\s+(?:the\s+)?(?P<title>.{2,50})(?:\s+window)?$|^focus\s+(?:on\s+)?(?:the\s+)?(?P<title2>.{2,50})(?:\s+window)?$"),
        builder=lambda m, c: {"title": (m.group("title") or m.group("title2") or "").strip()} or None,
        confidence=0.7,
    ),
    Rule(
        name="minimize_window",
        intent="desktop",
        tool="minimize_window",
        pattern=_rx(r"^minimi[sz]e\s+(?:the\s+)?(?P<title>.{2,50}?)(?:\s+window)?$"),
        confidence=0.9,
    ),
    Rule(
        name="maximize_window",
        intent="desktop",
        tool="maximize_window",
        pattern=_rx(r"^maximi[sz]e\s+(?:the\s+)?(?P<title>.{2,50}?)(?:\s+window)?$"),
        confidence=0.9,
    ),

    # ---------------------------------------------------------------- power
    Rule(
        name="lock_screen",
        intent="power",
        tool="lock_screen",
        pattern=_rx(r"lock\s+(?:my\s+|the\s+)?(?:pc|computer|screen|laptop|machine)"),
        confidence=0.98,
    ),
    Rule(
        name="shutdown",
        intent="power",
        tool="shutdown_pc",
        pattern=_rx(r"(?:shut\s*down|power\s+off|turn\s+off)\s+(?:my\s+|the\s+)?(?:pc|computer|laptop|machine|system)"),
        confidence=0.98,
    ),
    Rule(
        name="restart",
        intent="power",
        tool="restart_pc",
        pattern=_rx(r"(?:restart|reboot)\s+(?:my\s+|the\s+)?(?:pc|computer|laptop|machine|system)"),
        confidence=0.98,
    ),
    Rule(
        name="sleep_pc",
        intent="power",
        tool="sleep_pc",
        pattern=_rx(r"(?:put\s+)?(?:my\s+|the\s+)?(?:pc|computer|laptop|machine)\s+to\s+sleep|^sleep\s+(?:the\s+)?(?:pc|computer)$"),
        confidence=0.97,
    ),

    # ------------------------------------------------------------ reminders
    Rule(
        name="reminder_in",
        intent="automation",
        tool="set_reminder",
        pattern=_rx(
            r"^remind\s+me\s+(?:to\s+(?P<text>.+?)\s+)?in\s+(?P<amount>[\w.]+)\s+(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"
            r"(?:\s+to\s+(?P<text2>.+))?$"
        ),
        builder=lambda m, c: _build_reminder_in(m, c) if not m.group("text2") else (
            (lambda s: {"text": m.group("text2").strip(" ."), "in_seconds": s} if s else None)(
                parse_duration_seconds(m.group("amount"), m.group("unit"))
            )
        ),
        confidence=0.97,
    ),
    Rule(
        name="reminder_at",
        intent="automation",
        tool="set_reminder",
        pattern=_rx(
            r"^remind\s+me\s+(?:to\s+(?P<text>.+?)\s+)?at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm|a\.m\.|p\.m\.)?"
            r"(?:\s+to\s+(?P<text3>.+))?$"
        ),
        builder=lambda m, c: _build_reminder_at(m, c) if not m.group("text3") else (
            _build_reminder_at(m, c) and {**_build_reminder_at(m, c), "text": m.group("text3").strip(" .")}
        ),
        confidence=0.96,
    ),
    Rule(
        name="timer",
        intent="automation",
        tool="set_timer",
        pattern=_rx(
            r"^(?:set\s+)?(?:a\s+)?timer\s+(?:for\s+)?(?P<amount>[\w.]+)\s+(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"
            r"(?:\s+(?:for|called|named)\s+(?P<label>.+))?$"
        ),
        builder=_build_timer,
        confidence=0.97,
    ),
    Rule(
        name="list_reminders",
        intent="automation",
        tool="list_reminders",
        pattern=_rx(r"(?:list|show|what are)\s+(?:my\s+|the\s+)?(?:reminders|timers|alarms)"),
        confidence=0.96,
    ),

    # ----------------------------------------------------------------- notes
    Rule(
        name="quick_note",
        intent="content",
        tool="quick_note",
        pattern=_rx(r"^(?:take\s+a\s+note|note\s+down|note\s+that|write\s+down|jot\s+down)[:\s]+(?P<text>.+)$"),
        confidence=0.95,
    ),

    # ----------------------------------------------------------- information
    Rule(
        name="weather",
        intent="info",
        tool="weather",
        pattern=_rx(
            r"(?:what(?:'s| is| will)?\s+)?(?:the\s+)?(?:weather|forecast|temperature)"
            r"(?:\s+(?:like\s+)?(?:today|tomorrow|outside|now))?"
            r"(?:\s+in\s+(?P<location>[\w .,-]{2,50}))?"
        ),
        builder=lambda m, c: {"location": m.group("location").strip()} if m.group("location") else {},
        confidence=0.93,
    ),
    Rule(
        name="news",
        intent="info",
        tool="news",
        pattern=_rx(r"^(?:(?:show|tell|give)\s+me\s+)?(?:the\s+|today'?s\s+|latest\s+)*(?:news|headlines)(?:\s+(?:about|on)\s+(?P<topic>.+))?$"),
        builder=lambda m, c: {"topic": m.group("topic").strip()} if m.group("topic") else {},
        confidence=0.93,
    ),
    Rule(
        name="time",
        intent="info",
        tool="time",
        pattern=_rx(r"(?:what\s+time\s+is\s+it|current\s+time|tell\s+me\s+the\s+time|^time$|what(?:'s| is)\s+the\s+time|today'?s\s+date|what(?:'s| is)\s+(?:the\s+|today'?s\s+)date|^date$)"),
        confidence=0.97,
    ),
    Rule(
        name="wikipedia",
        intent="info",
        tool="wikipedia",
        pattern=_rx(r"^(?:wikipedia|wiki)\s+(?P<q>.+)$|^(?:search\s+wikipedia\s+for)\s+(?P<q2>.+)$"),
        builder=lambda m, c: {"topic": (m.group("q") or m.group("q2") or "").strip(" ?.")} or None,
        confidence=0.96,
    ),
    Rule(
        name="who_is",
        intent="info",
        tool="wikipedia",
        pattern=_rx(r"^who\s+(?:is|was|are)\s+(?P<q>[\w .,'-]{2,60})$"),
        builder=_passthrough_query("topic"),
        confidence=0.8,
    ),

    # ------------------------------------------------------------ web search
    Rule(
        name="web_search_explicit",
        intent="web",
        tool="web_search",
        # Negative lookahead: "search (for) files ..." belongs to the file tools.
        pattern=_rx(r"^(?:search(?:\s+the)?(?:\s+web|\s+internet|\s+online)?\s+(?:for\s+)?|google\s+|look\s+up\s+)(?!(?:for\s+)?files?\b)(?P<q>.+)$"),
        builder=_passthrough_query("query"),
        confidence=0.9,
    ),

    # ------------------------------------------------------------ calculator
    Rule(
        name="calculator",
        intent="math",
        tool="calculator",
        pattern=_rx(r"^(?:calculate|compute|eval(?:uate)?|what\s+is|what's|solve)\s+(?P<expr>[\d\s()+\-*/.%^]+)$"),
        builder=_build_calc,
        confidence=0.96,
    ),
    Rule(
        name="calculator_words",
        intent="math",
        tool="calculator",
        pattern=_rx(
            r"(\d[\d\s.,]*\s*(?:multiplied\s+by|divided\s+by|plus|minus|times|"
            r"ko|को|se|से|me|में|mein)\s*[\d\s.,]*\d)"
        ),
        builder=_build_calc_words,
        confidence=0.9,
    ),
    Rule(
        name="calculator_bare",
        intent="math",
        tool="calculator",
        pattern=_rx(r"^(?P<expr>[-+]?\d[\d\s()+\-*/.%^]*[\d)])$"),
        builder=_build_calc,
        confidence=0.9,
    ),

    # -------------------------------------------------------------- content
    Rule(
        name="create_presentation",
        intent="content",
        tool="create_presentation",
        pattern=_rx(
            r"^(?:create|make|build|prepare|generate)\s+(?:a\s+|an\s+)?"
            r"(?:ppt|pptx|powerpoint|presentation|slide\s*deck|slides|deck)\s+"
            r"(?:presentation\s+)?(?:about|on|for|of)\s+(?P<topic>.+)$"
        ),
        builder=lambda m, c: {"title": m.group("topic").strip(" ."), "topic": m.group("topic").strip(" .")},
        confidence=0.97,
        needs_generation=True,
    ),
    Rule(
        name="write_document",
        intent="content",
        tool="write_document",
        pattern=_rx(
            r"^(?:write|create|draft|make)\s+(?:a\s+|an\s+)?"
            r"(?:document|doc|essay|report|letter|article|word\s+(?:doc|document|file))\s+"
            r"(?:about|on|for)\s+(?P<topic>.+)$"
        ),
        builder=lambda m, c: {"title": m.group("topic").strip(" ."), "content": ""},
        confidence=0.96,
        needs_generation=True,
    ),
    Rule(
        name="create_spreadsheet",
        intent="content",
        tool="create_spreadsheet",
        pattern=_rx(
            r"^(?:create|make|build)\s+(?:a\s+|an\s+)?"
            r"(?:spreadsheet|excel(?:\s+(?:sheet|file))?|xlsx|csv)\s+"
            r"(?:about|on|for|of)\s+(?P<topic>.+)$"
        ),
        builder=lambda m, c: {"title": m.group("topic").strip(" .")},
        confidence=0.95,
        needs_generation=True,
    ),
    Rule(
        name="write_code",
        intent="code",
        tool="write_code",
        pattern=_rx(
            r"^(?:write|create|make|generate)\s+(?:a\s+|an\s+|some\s+)?"
            r"(?:(?P<language>python|javascript|js|html|css|c\+\+|java|go|rust|bash)\s+)?"
            r"(?:code|script|program|function|app(?:lication)?)\s+"
            r"(?:that|to|which|for)\s+(?P<task>.+)$"
        ),
        builder=lambda m, c: {
            "task": m.group("task").strip(" ."),
            **({"language": m.group("language")} if m.group("language") else {}),
        },
        confidence=0.94,
        needs_generation=True,
    ),
    Rule(
        name="scaffold_project",
        intent="code",
        tool="scaffold_project",
        pattern=_rx(r"^(?:scaffold|bootstrap|set\s+up)\s+(?:a\s+|an\s+)?(?:new\s+)?(?P<template>python|web|node|api|fastapi)?\s*project(?:\s+(?:called|named)\s+(?P<name>[\w-]+))?$"),
        builder=lambda m, c: {
            "name": (m.group("name") or "new-project").strip(),
            **({"template": {"api": "python-api", "fastapi": "python-api"}.get(m.group("template"), m.group("template"))}
               if m.group("template") else {}),
        },
        confidence=0.9,
    ),

    # ----------------------------------------------------------------- files
    Rule(
        name="list_files",
        intent="files",
        tool="list_directory",
        pattern=_rx(r"^(?:list|show)\s+(?:my\s+|the\s+)?files(?:\s+in\s+(?P<path>.+))?$"),
        builder=lambda m, c: {"path": m.group("path").strip()} if m.group("path") else {},
        confidence=0.92,
    ),
    Rule(
        name="find_files",
        intent="files",
        tool="search_files",
        pattern=_rx(r"^(?:find|search(?:\s+for)?)\s+(?:a\s+)?files?\s+(?:named|called|matching)\s+(?P<pattern>.+)$"),
        confidence=0.94,
    ),

    # ---------------------------------------------------------------- system
    Rule(
        name="system_info",
        intent="system",
        tool="system_info",
        pattern=_rx(
            r"(?:system\s+(?:info|information|specs|status)|my\s+(?:pc|computer|laptop)\s+specs"
            r"|(?:cpu|memory|ram|disk)\s+usage|how\s+much\s+(?:ram|memory|disk)"
            r"|(?:what|which)\s+operating\s+system|operating\s+system\s+am\s+i)"
        ),
        confidence=0.93,
    ),
    Rule(
        name="processes",
        intent="system",
        tool="list_processes",
        pattern=_rx(r"(?:list|show|what)\s+(?:are\s+)?(?:the\s+|my\s+)?(?:running\s+)?processes|what(?:'s| is)\s+(?:using|eating)\s+(?:my\s+)?(?:cpu|memory|ram)"),
        confidence=0.93,
    ),
    Rule(
        name="network_info",
        intent="system",
        tool="network_info",
        pattern=_rx(r"(?:what(?:'s| is)\s+my\s+ip|ip\s+address|network\s+(?:info|status)|am\s+i\s+(?:online|connected))"),
        confidence=0.95,
    ),

    # --------------------------------------------------------------- convert
    Rule(
        name="unit_convert",
        intent="math",
        tool="unit_converter",
        pattern=_rx(r"^convert\s+(?P<value>[\d.]+)\s*(?P<from_unit>[\w°]+)\s+(?:to|into|in)\s+(?P<to_unit>[\w°]+)$"),
        confidence=0.95,
    ),
]


def rule_names() -> list[str]:
    return [r.name for r in RULES]
