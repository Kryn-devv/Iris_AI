"""Deterministic localization of common tool acknowledgements.

Tools speak short English acks ("Opened YouTube.", "Timer set for 5
minutes."). When the user is talking in Hindi or Hinglish the kernel routes
those acks through :func:`localize_ack`, which rewrites only the handful of
phrases it knows with full confidence. Anything unrecognised passes through
unchanged in English — no guessing, no machine translation.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Tuple, Union

from iris.app.language.models import LanguageStyle

_Responder = Callable[["re.Match[str]"], str]

#: Ordered (pattern, {style: responder}) pairs. Patterns must consume the
#: whole ack (fullmatch) so partial or composite sentences pass through.
_ACK_RULES: List[Tuple["re.Pattern[str]", Dict[LanguageStyle, _Responder]]] = [
    (
        re.compile(r"Done\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: "Ho gaya.",
            LanguageStyle.HINDI: lambda m: "हो गया।",
        },
    ),
    (
        re.compile(r"Opened (.+?)\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: f"{m.group(1)} khol diya.",
            LanguageStyle.HINDI: lambda m: f"{m.group(1)} खोल दिया।",
        },
    ),
    (
        re.compile(r"Created (.+?)\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: f"{m.group(1)} bana diya.",
            LanguageStyle.HINDI: lambda m: f"{m.group(1)} बना दिया।",
        },
    ),
    (
        re.compile(r"Turned on (.+?)\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: f"{m.group(1)} chalu kar diya.",
            LanguageStyle.HINDI: lambda m: f"{m.group(1)} चालू कर दिया।",
        },
    ),
    (
        re.compile(r"Turned off (.+?)\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: f"{m.group(1)} band kar diya.",
            LanguageStyle.HINDI: lambda m: f"{m.group(1)} बंद कर दिया।",
        },
    ),
    (
        re.compile(r"Turned the volume up(?: to (\d+) percent)?\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: (
                f"Volume badha diya — {m.group(1)} percent." if m.group(1) else "Volume badha diya."
            ),
            LanguageStyle.HINDI: lambda m: (
                f"वॉल्यूम बढ़ा दिया — {m.group(1)} प्रतिशत।" if m.group(1) else "वॉल्यूम बढ़ा दिया।"
            ),
        },
    ),
    (
        re.compile(r"Turned the volume down(?: to (\d+) percent)?\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: (
                f"Volume kam kar diya — {m.group(1)} percent." if m.group(1) else "Volume kam kar diya."
            ),
            LanguageStyle.HINDI: lambda m: (
                f"वॉल्यूम कम कर दिया — {m.group(1)} प्रतिशत।" if m.group(1) else "वॉल्यूम कम कर दिया।"
            ),
        },
    ),
    (
        re.compile(r"Timer set for (.+?)\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: f"Timer laga diya — {m.group(1)}.",
            LanguageStyle.HINDI: lambda m: f"टाइमर लगा दिया — {m.group(1)}।",
        },
    ),
    (
        re.compile(r"Screenshot saved\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: "Screenshot le liya.",
            LanguageStyle.HINDI: lambda m: "स्क्रीनशॉट ले लिया।",
        },
    ),
    (
        re.compile(r"Playing (.+?) on YouTube\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: f"YouTube par {m.group(1)} chala diya.",
            LanguageStyle.HINDI: lambda m: f"YouTube पर {m.group(1)} चला दिया।",
        },
    ),
    (
        re.compile(r"Locked the screen\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: "Screen lock kar di.",
            LanguageStyle.HINDI: lambda m: "स्क्रीन लॉक कर दी।",
        },
    ),
    (
        re.compile(r"Closed (.+?)\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: f"{m.group(1)} band kar diya.",
            LanguageStyle.HINDI: lambda m: f"{m.group(1)} बंद कर दिया।",
        },
    ),
    (
        re.compile(r"Noted\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: "Note kar liya.",
            LanguageStyle.HINDI: lambda m: "नोट कर लिया।",
        },
    ),
    (
        re.compile(r"Cancelled\.?"),
        {
            LanguageStyle.HINGLISH: lambda m: "Cancel kar diya.",
            LanguageStyle.HINDI: lambda m: "रद्द कर दिया।",
        },
    ),
]


def _normalize_style(style: Union[LanguageStyle, str, None]) -> LanguageStyle:
    if isinstance(style, str):
        try:
            style = LanguageStyle(style.upper())
        except ValueError:
            return LanguageStyle.ENGLISH
    if style == LanguageStyle.MIXED:
        return LanguageStyle.HINGLISH
    if style in (LanguageStyle.HINDI, LanguageStyle.HINGLISH):
        return style
    return LanguageStyle.ENGLISH


def localize_ack(text: str, style: Union[LanguageStyle, str, None]) -> str:
    """Rewrite a known English tool ack into Hindi/Hinglish, else pass through.

    Only exact, whole-string matches of the templates above are rewritten;
    everything else (including English style) is returned untouched.
    """
    if not text:
        return text
    resolved = _normalize_style(style)
    if resolved not in (LanguageStyle.HINDI, LanguageStyle.HINGLISH):
        return text
    candidate = text.strip()
    for pattern, responders in _ACK_RULES:
        m = pattern.fullmatch(candidate)
        if m:
            return responders[resolved](m)
    return text
