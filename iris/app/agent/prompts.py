"""Centralized system prompts for the IRIS agent.

The character brief itself lives in :mod:`iris.app.agent.persona` — it is long,
it is prose, and it is the single thing that decides whether Iris sounds like a
person or like a form. This module keeps the assembly: slots filled, execution
context appended, and a model control suffix bolted on for local models that
need one.

``get_system_prompt`` keeps its old signature, so the context assembler and the
kernel need no changes. The CONTENT_* prompts below are untouched: they build
documents and slides, where structure is the point.
"""

from __future__ import annotations

from iris.app.agent.persona import CHARACTER, character_prompt
from iris.app.core.config import settings

#: Kept as an alias: older code and tests import this name.
SYSTEM_PROMPT_IRIS_CORE = CHARACTER


def get_system_prompt(custom_context: str = "") -> str:
    """The character brief, plus whatever she knows right now."""
    base = character_prompt()
    if custom_context:
        base = f"{base}\n\n# What you know right now\n{custom_context}"
    # Model control tokens (e.g. Qwen3's "/no_think", which stops a local model
    # reasoning for ten seconds before saying "morning"). Empty by default.
    suffix = (getattr(settings, "PROMPT_SUFFIX", "") or "").strip()
    return f"{base}\n\n{suffix}" if suffix else base


CONTENT_SLIDES_PROMPT = """Create the content for a presentation about: {topic}
Write all human-readable content in {lang_instruction}.
Return JSON only: {{"title": str, "slides": [{{"title": str, "bullets": [str, ...], "notes": str}}]}}
6-10 slides, 3-5 tight bullets each, an agenda slide after the title, and a closing summary slide."""

CONTENT_DOCUMENT_PROMPT = """Write a well-structured document about: {topic}
Write all human-readable content in {lang_instruction}.
Use markdown-style structure: '# ' headings, '- ' bullet lists and plain paragraphs.
Aim for 400-800 words of genuinely informative content. Return the document text only."""

CONTENT_CODE_PROMPT = """Write a complete, runnable {language} program for this task: {task}
Write all human-readable content (comments, docstrings, notes) in {lang_instruction}.
Requirements: production quality, comments where helpful, no placeholders.
Return JSON only: {{"filename": str, "code": str, "notes": str}}"""

CONTENT_SPREADSHEET_PROMPT = """Design a spreadsheet about: {topic}
Write all human-readable content in {lang_instruction}.
Return JSON only: {{"title": str, "headers": [str,...], "rows": [[...], ...]}} with 5-15 realistic rows."""


#: How the ``{lang_instruction}`` placeholder reads for each target language.
_LANGUAGE_INSTRUCTIONS = {
    "en": "English",
    "hi": "Hindi (Devanagari)",
    "hinglish": "Hinglish (Hindi-English mix, Latin script)",
}


def language_instruction(lang_code: str) -> str:
    """Map a target response language code to a prompt-ready instruction."""
    return _LANGUAGE_INSTRUCTIONS.get((lang_code or "en").lower(), "English")
