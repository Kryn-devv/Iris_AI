"""Deterministic personality layer: instant, offline small talk.

Greetings, thanks, identity questions and a few delights answer immediately
without any model. Anything not matched here flows onward to the agent loop.

Responders are keyed by :class:`LanguageStyle` so greetings, thanks and
identity answers come back in the user's own register (English, Hindi or
Hinglish). Rules without a variant for the requested style fall back to
English.
"""

from __future__ import annotations

import datetime
import random
import re
from typing import Optional, Union

from iris.app.core.config import settings
from iris.app.language.models import LanguageStyle

_JOKES = (
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break… it said 'no problem, I'll go to sleep.'",
    "There are 10 types of people: those who understand binary and those who don't.",
    "Why did the developer go broke? Because they used up all their cache.",
    "I would tell you a UDP joke, but you might not get it.",
    "A SQL query walks into a bar, goes up to two tables and asks: 'Can I join you?'",
)

_FACTS = (
    "Honey never spoils — edible honey has been found in 3,000-year-old Egyptian tombs.",
    "Octopuses have three hearts and blue blood.",
    "The first computer bug was an actual moth, found in a relay in 1947.",
    "A day on Venus is longer than a year on Venus.",
    "Sharks existed before trees did.",
)


def _greeting_for_hour(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 22:
        return "Good evening"
    return "Hello"


class _Rule:
    """A trigger pattern plus one responder per language style.

    ``responders`` is either a single callable (English-only) or a dict
    keyed by :class:`LanguageStyle`; missing styles fall back to English.
    """

    def __init__(self, pattern: str, responders):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        if not isinstance(responders, dict):
            responders = {LanguageStyle.ENGLISH: responders}
        self.responders = responders

    def respond(self, match, style: LanguageStyle) -> str:
        responder = self.responders.get(style) or self.responders[LanguageStyle.ENGLISH]
        return responder(match)


def _name() -> str:
    return settings.ASSISTANT_NAME


_RULES = [
    _Rule(
        # (?=$|[\s!,.?]) instead of \b: Devanagari vowel signs are not word
        # characters, so \b never matches after "नमस्ते".
        r"^(hi|hii+|hello|hey|yo|hola|namaste|नमस्ते|नमस्कार"
        r"|good (morning|afternoon|evening))(?=$|[\s!,.?]).{0,15}$",
        {
            LanguageStyle.ENGLISH: lambda m: (
                f"{_greeting_for_hour(datetime.datetime.now().hour)}! I'm {_name()}. "
                "What can I do for you?"
            ),
            LanguageStyle.HINGLISH: lambda m: (
                f"Namaste! Main {_name()} hoon. Batao, kya karna hai?"
            ),
            LanguageStyle.HINDI: lambda m: (
                f"नमस्ते! मैं {_name()} हूँ। बताइए, मैं क्या मदद करूँ?"
            ),
        },
    ),
    _Rule(
        r"^(who are you|what are you|introduce yourself|tell me about yourself"
        r"|tum kaun ho|aap kaun ho|kaun ho tum|तुम कौन हो|आप कौन हैं)\??$",
        {
            LanguageStyle.ENGLISH: lambda m: (
                f"I'm {_name()} — your personal desktop assistant. I can open apps and websites, control "
                "volume and windows, search the web, check weather and news, set reminders, build "
                "presentations and documents, write code, and much more. Try: \"open YouTube\" or "
                "\"make a ppt about space travel\"."
            ),
            LanguageStyle.HINGLISH: lambda m: (
                f"Main {_name()} hoon — aapka personal desktop assistant. Apps aur websites kholna, "
                "volume control, web search, weather, reminders, presentations, code — sab mujhse ho "
                "jata hai. Try karo: \"youtube kholo\" ya \"space travel par ek ppt banao\"."
            ),
            LanguageStyle.HINDI: lambda m: (
                f"मैं {_name()} हूँ — आपका निजी डेस्कटॉप असिस्टेंट। ऐप्स और वेबसाइट खोलना, "
                "वॉल्यूम बदलना, वेब खोज, मौसम, रिमाइंडर, प्रेज़ेंटेशन और कोड — यह सब मुझसे कहिए। "
                "आज़माइए: \"youtube kholo\"।"
            ),
        },
    ),
    _Rule(
        r"^(what can you do|help|what do you do|show me what you can do|capabilities)\??$",
        lambda m: (
            "Here's a taste of what I can do:\n"
            "• \"open youtube\" / \"open notepad\" — apps & websites\n"
            "• \"play lo-fi beats on youtube\"\n"
            "• \"volume up\", \"mute\", \"take a screenshot\", \"lock my pc\"\n"
            "• \"what's the weather in Mumbai\", \"news about AI\"\n"
            "• \"remind me in 20 minutes to stretch\", \"set a timer for 5 minutes\"\n"
            "• \"make a ppt about renewable energy\"\n"
            "• \"write a python script that renames files\"\n"
            "• \"search the web for best laptops\"\n"
            "…and I understand plain conversation too."
        ),
    ),
    _Rule(
        r"^(what('?| i)s your name|your name)\??$",
        lambda m: f"I'm {_name()} — nice to meet you.",
    ),
    _Rule(
        r"^(how are you|how's it going|how are you doing|kaise ho|kya haal hai"
        r"|aap kaise (ho|hain)|आप कैसे हैं|कैसे हो)\??.{0,10}$",
        {
            LanguageStyle.ENGLISH: lambda m: (
                "Running at full capacity and happy to help. What's on your mind?"
            ),
            LanguageStyle.HINGLISH: lambda m: (
                "Bas badhiya! Sab systems ready hain. Batao, kya karna hai?"
            ),
            LanguageStyle.HINDI: lambda m: (
                "मैं बिल्कुल ठीक हूँ, धन्यवाद! बताइए, क्या मदद करूँ?"
            ),
        },
    ),
    _Rule(
        r"^(thanks|thank you|thx|ty|great job|well done|awesome|nice|perfect"
        r"|shukriya|dhanyavad|dhanyawad|शुक्रिया|धन्यवाद)(?=$|[\s!,.?]).{0,20}$",
        {
            LanguageStyle.ENGLISH: lambda m: random.choice(
                ("Anytime!", "Happy to help!", "You're welcome!", "Glad it worked!")
            ),
            LanguageStyle.HINGLISH: lambda m: random.choice(
                ("Koi baat nahi!", "Arre, kabhi bhi!", "Khushi hui madad karke!")
            ),
            LanguageStyle.HINDI: lambda m: random.choice(
                ("कोई बात नहीं!", "आपका स्वागत है!", "खुशी हुई मदद करके!")
            ),
        },
    ),
    _Rule(
        r"^(bye|goodbye|good night|see you|later|gn)\b.{0,10}$",
        lambda m: "Goodbye! I'll be right here when you need me.",
    ),
    _Rule(
        r"tell me a joke|make me laugh|another joke|^joke$",
        lambda m: random.choice(_JOKES),
    ),
    _Rule(
        r"(tell me a|random|fun) fact|^fact$",
        lambda m: random.choice(_FACTS),
    ),
    _Rule(
        r"^(i love you|do you love me)\b.*$",
        lambda m: "I'm flattered! I'm all circuits and code, but I'm 100% here for you.",
    ),
    _Rule(
        r"^(are you (there|awake|listening|online))\??$",
        lambda m: "Always. What do you need?",
    ),
]


def _normalize_style(style: Union[LanguageStyle, str, None]) -> LanguageStyle:
    """Coerce the caller's style (enum or raw string) to a responder key."""
    if isinstance(style, str):
        try:
            style = LanguageStyle(style.upper())
        except ValueError:
            style = LanguageStyle.ENGLISH
    if style == LanguageStyle.MIXED:
        return LanguageStyle.HINGLISH
    if style in (LanguageStyle.HINDI, LanguageStyle.HINGLISH):
        return style
    return LanguageStyle.ENGLISH


def match_smalltalk(
    text: str, style: Union[LanguageStyle, str, None] = LanguageStyle.ENGLISH
) -> Optional[str]:
    """Return an instant reply for conversational pleasantries, else ``None``.

    ``style`` selects the language register of the reply (MIXED maps to
    HINGLISH; anything unrecognised falls back to ENGLISH).
    """
    cleaned = (text or "").strip().rstrip(".!").strip()
    if not cleaned or len(cleaned) > 80:
        return None
    resolved_style = _normalize_style(style)
    for rule in _RULES:
        m = rule.pattern.search(cleaned)
        if m:
            return rule.respond(m, resolved_style)
    return None
