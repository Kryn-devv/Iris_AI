"""Deterministic personality layer: instant, offline small talk.

Greetings, thanks, identity questions and a few delights answer immediately
without any model. Anything not matched here flows onward to the agent loop.
"""

from __future__ import annotations

import datetime
import random
import re
from typing import Optional

from iris.app.core.config import settings

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
    def __init__(self, pattern: str, responder):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.responder = responder


def _name() -> str:
    return settings.ASSISTANT_NAME


_RULES = [
    _Rule(
        r"^(hi|hii+|hello|hey|yo|hola|namaste|good (morning|afternoon|evening))\b.{0,15}$",
        lambda m: f"{_greeting_for_hour(datetime.datetime.now().hour)}! I'm {_name()}. What can I do for you?",
    ),
    _Rule(
        r"^(who are you|what are you|introduce yourself|tell me about yourself)\??$",
        lambda m: (
            f"I'm {_name()} — your personal desktop assistant. I can open apps and websites, control "
            "volume and windows, search the web, check weather and news, set reminders, build "
            "presentations and documents, write code, and much more. Try: \"open YouTube\" or "
            "\"make a ppt about space travel\"."
        ),
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
        r"^(how are you|how's it going|how are you doing|kaise ho)\??.{0,10}$",
        lambda m: "Running at full capacity and happy to help. What's on your mind?",
    ),
    _Rule(
        r"^(thanks|thank you|thx|ty|great job|well done|awesome|nice|perfect)\b.{0,20}$",
        lambda m: random.choice(("Anytime!", "Happy to help!", "You're welcome!", "Glad it worked!")),
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


def match_smalltalk(text: str) -> Optional[str]:
    """Return an instant reply for conversational pleasantries, else ``None``."""
    cleaned = (text or "").strip().rstrip(".!").strip()
    if not cleaned or len(cleaned) > 80:
        return None
    for rule in _RULES:
        m = rule.pattern.search(cleaned)
        if m:
            return rule.responder(m)
    return None
