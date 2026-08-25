"""Presentation creation tools for IRIS.

Builds slide decks from a title, an optional explicit slide list, or a bare
topic. Two rendering engines are supported:

* **PPTX** via ``python-pptx`` (optional dependency) — a 16:9 deck with a
  dark or light theme, accent title bars, consistent margins and speaker
  notes.
* **HTML** (always available, zero dependencies) — a single self-contained
  ``.html`` file containing a keyboard-navigable full-screen slide deck with
  embedded CSS and JavaScript.

The outline generator, slug helper and HTML builder are pure functions so the
deterministic NLU kernel (and the tests) can exercise them without touching
the filesystem.
"""

from __future__ import annotations

import html as html_lib
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

from iris.app.core import paths
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import try_import
from iris.app.core.security import PermissionLevel, SandboxError, default_path_sandbox
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.content.presentation")


# =============================================================================
# Pure helpers
# =============================================================================

def slugify(text: str, *, max_length: int = 60) -> str:
    """Turn arbitrary text into a safe, lowercase filename slug.

    Accents are folded to ASCII, anything that is not alphanumeric becomes a
    single hyphen, and the result is trimmed to ``max_length``. An empty or
    fully-symbolic input yields ``"untitled"``.
    """
    folded = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_length].rstrip("-") or "untitled"


#: Words too generic to become content-section anchors.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "at",
        "with", "about", "into", "over", "from", "by", "my", "your", "our",
        "their", "his", "her", "its", "is", "are", "was", "be", "how", "what",
        "why", "intro", "introduction", "overview", "presentation", "deck",
        "slides", "ppt",
    }
)


def _topic_keywords(topic: str) -> List[str]:
    """Significant words of a topic, in order, stopwords removed."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9']*", (topic or "").lower())
    keywords = [w for w in words if w not in _STOPWORDS]
    return keywords or words[:1]


def generate_outline(topic: str) -> List[Dict[str, Any]]:
    """Build a sensible deterministic slide outline for a bare topic.

    Structure: title slide, agenda, three to five content sections derived
    from the topic's significant words, a summary and a closing thank-you /
    Q&A slide. The same topic always yields the same outline, so the kernel
    can call this without an LLM in the loop.
    """
    topic = (topic or "").strip() or "Untitled Topic"
    display = topic if topic[:1].isupper() else topic[:1].upper() + topic[1:]
    keywords = _topic_keywords(topic)
    section_count = min(5, max(3, len(keywords)))

    key_bullets = [
        f"{kw[:1].upper()}{kw[1:]} — what it is and why it matters"
        for kw in keywords[:5]
    ] or [f"The core building blocks of {display}"]

    section_templates: List[tuple[str, List[str]]] = [
        (
            f"What is {display}?",
            [
                f"Definition and scope of {display}",
                "Where it fits in the bigger picture",
                "A brief history and the current state of the art",
            ],
        ),
        (
            f"Why {display} matters",
            [
                "Key benefits and opportunities",
                "Impact on people, teams and outcomes",
                "The cost of ignoring it",
            ],
        ),
        (f"Key elements of {display}", key_bullets),
        (
            "Challenges and considerations",
            [
                "Common pitfalls and how to avoid them",
                "Constraints, risks and trade-offs",
                "How to measure real progress",
            ],
        ),
        (
            f"Getting started with {display}",
            [
                "First practical steps you can take today",
                "Tools and resources worth knowing",
                "A simple 30-60-90 day plan",
            ],
        ),
    ]
    sections = [
        {
            "title": heading,
            "bullets": list(bullets),
            "notes": f"Walk the audience through {heading.rstrip('?').lower()}, "
                     "pausing for questions before moving on.",
        }
        for heading, bullets in section_templates[:section_count]
    ]

    outline: List[Dict[str, Any]] = [
        {
            "title": display,
            "bullets": [f"An overview of {display}", "Prepared with IRIS"],
            "notes": f"Welcome the audience and introduce the topic: {display}.",
        },
        {
            "title": "Agenda",
            "bullets": [s["title"] for s in sections] + ["Summary"],
            "notes": "Preview the flow of the talk so the audience knows what to expect.",
        },
        *sections,
        {
            "title": "Summary",
            "bullets": [f"What we covered about {display}"]
            + [s["title"] for s in sections[:3]]
            + ["Key takeaway: start small, iterate quickly"],
            "notes": "Recap the main points in one breath each.",
        },
        {
            "title": "Thank You — Q&A",
            "bullets": [
                "Thanks for your time and attention",
                "Questions, ideas and feedback are very welcome",
            ],
            "notes": "Open the floor for questions and discussion.",
        },
    ]
    return outline


def normalize_slides(raw: Any) -> List[Dict[str, Any]]:
    """Coerce a model- or user-supplied slide list into a canonical shape.

    Each entry becomes ``{"title": str, "bullets": [str], "notes": str}``.
    Strings are treated as bare slide titles. Anything else raises
    :class:`ToolError`.
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ToolError(
            "The 'slides' argument must be a list of slide objects.",
            speech="The slides you gave me were in the wrong shape.",
        )
    slides: List[Dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            slides.append({"title": item.strip() or f"Slide {index}", "bullets": [], "notes": ""})
            continue
        if not isinstance(item, dict):
            raise ToolError(
                f"Slide {index} must be an object with title/bullets/notes, got {type(item).__name__}.",
                speech=f"Slide {index} was in the wrong shape.",
            )
        title = str(item.get("title") or "").strip() or f"Slide {index}"
        bullets_raw = item.get("bullets") or []
        if isinstance(bullets_raw, str):
            bullets_raw = [bullets_raw]
        bullets = [str(b).strip() for b in bullets_raw if str(b).strip()]
        notes = str(item.get("notes") or "").strip()
        slides.append({"title": title, "bullets": bullets, "notes": notes})
    if not slides:
        raise ToolError("The slide list was empty.", speech="There were no slides to build.")
    return slides


# =============================================================================
# HTML deck builder (zero-dependency fallback, always available)
# =============================================================================

_HTML_PALETTES: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg1": "#0B1020", "bg2": "#141B33", "accent": "#5EEAD4",
        "text": "#F8FAFC", "muted": "#94A3B8", "panel": "rgba(255,255,255,0.045)",
    },
    "light": {
        "bg1": "#F8FAFC", "bg2": "#E2E8F0", "accent": "#0F766E",
        "text": "#0F172A", "muted": "#475569", "panel": "rgba(15,23,42,0.05)",
    },
}

_DECK_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; overflow: hidden; }
body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, system-ui, sans-serif;
  background: radial-gradient(ellipse at 25% 10%, var(--bg2) 0%, var(--bg1) 65%);
  color: var(--text);
}
.slide {
  position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; opacity: 0; visibility: hidden;
  transition: opacity .35s ease; padding: 5vh 8vw 10vh;
}
.slide.active { opacity: 1; visibility: visible; }
.slide-inner { width: 100%; max-width: 1100px; }
.title-slide .slide-inner { text-align: center; }
h1 { font-size: clamp(2.4rem, 6vw, 4.2rem); font-weight: 700; letter-spacing: -0.02em; }
h2 { font-size: clamp(1.8rem, 4vw, 2.8rem); font-weight: 650; letter-spacing: -0.01em; }
.accent-bar {
  width: 96px; height: 6px; border-radius: 3px; background: var(--accent);
  margin: 1.1rem 0 1.6rem;
}
.title-slide .accent-bar { margin-left: auto; margin-right: auto; }
ul.bullets { list-style: none; margin-top: .4rem; }
ul.bullets li {
  font-size: clamp(1.05rem, 2.2vw, 1.5rem); line-height: 1.55; color: var(--text);
  padding: .45rem 0 .45rem 1.9rem; position: relative;
}
ul.bullets li::before {
  content: ""; position: absolute; left: .4rem; top: 1.05em;
  width: .55em; height: .55em; border-radius: 50%;
  background: var(--accent); transform: translateY(-50%); opacity: .9;
}
.title-slide ul.bullets li { color: var(--muted); padding-left: 0; }
.title-slide ul.bullets li::before { display: none; }
aside.notes {
  display: none; margin-top: 2rem; padding: 1rem 1.2rem; border-radius: 10px;
  background: var(--panel); color: var(--muted); font-size: .95rem;
  border-left: 3px solid var(--accent);
}
body.show-notes aside.notes { display: block; }
footer {
  position: fixed; left: 0; right: 0; bottom: 0; display: flex;
  justify-content: space-between; align-items: center; padding: .8rem 1.6rem;
  color: var(--muted); font-size: .82rem; letter-spacing: .04em;
  user-select: none; z-index: 5;
}
footer .brand b { color: var(--accent); font-weight: 600; }
.hint { opacity: .75; }
"""

_DECK_JS = """
(function () {
  "use strict";
  var slides = Array.prototype.slice.call(document.querySelectorAll(".slide"));
  var counter = document.getElementById("counter");
  var index = 0;
  function show(n) {
    index = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach(function (slide, i) { slide.classList.toggle("active", i === index); });
    if (counter) { counter.textContent = (index + 1) + " / " + slides.length; }
    if (history.replaceState) { history.replaceState(null, "", "#" + (index + 1)); }
  }
  function next() { show(index + 1); }
  function prev() { show(index - 1); }
  // Keyboard navigation: arrow keys, space, page keys, home/end, N for notes.
  document.addEventListener("keydown", function (event) {
    var key = event.key;
    if (key === "ArrowRight" || key === "ArrowDown" || key === " " || key === "PageDown" || key === "Enter") {
      event.preventDefault(); next();
    } else if (key === "ArrowLeft" || key === "ArrowUp" || key === "PageUp" || key === "Backspace") {
      event.preventDefault(); prev();
    } else if (key === "Home") { show(0); }
    else if (key === "End") { show(slides.length - 1); }
    else if (key === "n" || key === "N") { document.body.classList.toggle("show-notes"); }
  });
  // Click navigation: right half advances, left half goes back.
  document.addEventListener("click", function (event) {
    if (event.clientX > window.innerWidth / 2) { next(); } else { prev(); }
  });
  var fromHash = parseInt((location.hash || "").replace("#", ""), 10);
  show(isNaN(fromHash) ? 0 : fromHash - 1);
})();
"""


def build_html_deck(title: str, slides: List[Dict[str, Any]], theme: str = "dark") -> str:
    """Render a self-contained, keyboard-navigable HTML slide deck.

    Pure function: no filesystem access, no external assets, everything is
    embedded so the resulting single file works offline in any browser.
    """
    palette = _HTML_PALETTES.get(theme, _HTML_PALETTES["dark"])
    esc = html_lib.escape
    root_vars = ":root{" + ";".join(f"--{key}:{value}" for key, value in palette.items()) + "}"

    sections: List[str] = []
    for index, slide in enumerate(slides):
        classes = "slide title-slide" if index == 0 else "slide"
        heading_tag = "h1" if index == 0 else "h2"
        parts = [f'<section class="{classes}" data-index="{index}"><div class="slide-inner">']
        parts.append(f"<{heading_tag}>{esc(str(slide.get('title') or ''))}</{heading_tag}>")
        parts.append('<div class="accent-bar"></div>')
        bullets = [str(b) for b in (slide.get("bullets") or []) if str(b).strip()]
        if bullets:
            items = "".join(f"<li>{esc(bullet)}</li>" for bullet in bullets)
            parts.append(f'<ul class="bullets">{items}</ul>')
        notes = str(slide.get("notes") or "").strip()
        if notes:
            parts.append(f'<aside class="notes">{esc(notes)}</aside>')
        parts.append("</div></section>")
        sections.append("".join(parts))

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(str(title))}</title>\n"
        f"<style>\n{root_vars}\n{_DECK_CSS}</style>\n</head>\n<body>\n"
        f'<main id="deck">{"".join(sections)}</main>\n'
        "<footer>"
        '<span class="brand">Made with <b>IRIS</b></span>'
        '<span class="hint">Use the arrow keys or click to navigate — press N for notes</span>'
        f'<span id="counter">1 / {len(slides)}</span>'
        "</footer>\n"
        f"<script>\n{_DECK_JS}</script>\n</body>\n</html>\n"
    )


# =============================================================================
# PPTX writer (optional python-pptx engine)
# =============================================================================

_PPTX_PALETTES: Dict[str, Dict[str, str]] = {
    "dark": {
        "background": "0B1020", "accent": "5EEAD4",
        "text": "FFFFFF", "muted": "94A3B8",
    },
    "light": {
        "background": "F8FAFC", "accent": "0F766E",
        "text": "0F172A", "muted": "475569",
    },
}


def _write_pptx(path: Path, title: str, slides: List[Dict[str, Any]], theme: str) -> None:
    """Write a styled 16:9 ``.pptx`` deck. Requires python-pptx (checked by caller)."""
    pptx_mod = try_import("pptx")
    util = try_import("pptx.util")
    dml_color = try_import("pptx.dml.color")
    enum_shapes = try_import("pptx.enum.shapes")
    enum_text = try_import("pptx.enum.text")
    if not all((pptx_mod, util, dml_color, enum_shapes, enum_text)):
        raise ToolError(
            "python-pptx is not installed. Install it with: pip install python-pptx",
            speech="I need the python-pptx package to build PowerPoint files.",
        )
    Inches, Pt = util.Inches, util.Pt
    RGBColor = dml_color.RGBColor
    MSO_SHAPE = enum_shapes.MSO_SHAPE
    PP_ALIGN = enum_text.PP_ALIGN

    palette = _PPTX_PALETTES.get(theme, _PPTX_PALETTES["dark"])
    bg_rgb = RGBColor.from_string(palette["background"])
    accent_rgb = RGBColor.from_string(palette["accent"])
    text_rgb = RGBColor.from_string(palette["text"])
    muted_rgb = RGBColor.from_string(palette["muted"])

    prs = pptx_mod.Presentation()
    prs.slide_width = Inches(13.333)  # 16:9 widescreen
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    def _fill_rect(slide: Any, left: Any, top: Any, width: Any, height: Any, rgb: Any) -> Any:
        shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb
        shape.line.fill.background()
        shape.shadow.inherit = False
        return shape

    for index, spec in enumerate(slides):
        slide = prs.slides.add_slide(blank_layout)
        _fill_rect(slide, 0, 0, prs.slide_width, prs.slide_height, bg_rgb)
        bullets = [str(b) for b in (spec.get("bullets") or []) if str(b).strip()]

        if index == 0:
            # Title slide: centered headline over a centered accent bar.
            box = slide.shapes.add_textbox(Inches(1.0), Inches(2.35), Inches(11.33), Inches(1.7))
            frame = box.text_frame
            frame.word_wrap = True
            para = frame.paragraphs[0]
            para.text = str(spec.get("title") or title)
            para.alignment = PP_ALIGN.CENTER
            para.font.size = Pt(48)
            para.font.bold = True
            para.font.color.rgb = text_rgb
            _fill_rect(slide, Inches(5.87), Inches(4.15), Inches(1.6), Inches(0.09), accent_rgb)
            if bullets:
                sub = slide.shapes.add_textbox(Inches(1.5), Inches(4.5), Inches(10.33), Inches(1.6))
                sub_frame = sub.text_frame
                sub_frame.word_wrap = True
                for j, line in enumerate(bullets):
                    para = sub_frame.paragraphs[0] if j == 0 else sub_frame.add_paragraph()
                    para.text = line
                    para.alignment = PP_ALIGN.CENTER
                    para.font.size = Pt(20)
                    para.font.color.rgb = muted_rgb
        else:
            # Content slide: title, accent underline bar, bullet body.
            box = slide.shapes.add_textbox(Inches(0.7), Inches(0.45), Inches(11.9), Inches(1.05))
            frame = box.text_frame
            frame.word_wrap = True
            para = frame.paragraphs[0]
            para.text = str(spec.get("title") or f"Slide {index + 1}")
            para.font.size = Pt(34)
            para.font.bold = True
            para.font.color.rgb = text_rgb
            _fill_rect(slide, Inches(0.75), Inches(1.5), Inches(2.9), Inches(0.07), accent_rgb)
            if bullets:
                body = slide.shapes.add_textbox(Inches(0.9), Inches(1.95), Inches(11.5), Inches(4.7))
                body_frame = body.text_frame
                body_frame.word_wrap = True
                for j, bullet in enumerate(bullets):
                    para = body_frame.paragraphs[0] if j == 0 else body_frame.add_paragraph()
                    para.text = f"•  {bullet}"
                    para.font.size = Pt(20)
                    para.font.color.rgb = text_rgb
                    para.space_after = Pt(12)

        # Footer: brand on the left, slide number on the right.
        foot = slide.shapes.add_textbox(Inches(0.7), Inches(6.98), Inches(11.9), Inches(0.35))
        foot_frame = foot.text_frame
        foot_para = foot_frame.paragraphs[0]
        foot_para.text = f"Made with IRIS   ·   {index + 1} / {len(slides)}"
        foot_para.font.size = Pt(10)
        foot_para.font.color.rgb = muted_rgb

        notes = str(spec.get("notes") or "").strip()
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    prs.save(str(path))


# =============================================================================
# Filesystem helper
# =============================================================================

def _deliverable_path(title: str, extension: str) -> Path:
    """Unique, sandbox-validated output path inside ``paths.outputs_dir()``."""
    out_dir = paths.outputs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{slugify(title)}-{stamp}"
    candidate = out_dir / f"{base}.{extension}"
    counter = 2
    while candidate.exists():
        candidate = out_dir / f"{base}-{counter}.{extension}"
        counter += 1
    try:
        return default_path_sandbox.resolve(candidate)
    except SandboxError as exc:
        raise ToolError(str(exc), speech="I couldn't write inside the allowed workspace.") from exc


# =============================================================================
# Tool
# =============================================================================

class CreatePresentationTool(BaseTool):
    """Create a slide deck as a .pptx (when python-pptx is installed) or a
    self-contained HTML deck that works everywhere with zero dependencies."""

    name = "create_presentation"
    description = "Creates a slide presentation (PowerPoint or HTML deck) from a title, topic or explicit slides."
    permission_level = PermissionLevel.WRITE_ACTION
    category = ToolCategory.CONTENT
    aliases = ("make_ppt", "create_ppt", "make_presentation", "make_slides", "create_deck")
    mutating = True
    examples = (
        ToolExample(
            utterance="make a presentation about renewable energy",
            arguments={"title": "Renewable Energy", "topic": "renewable energy"},
        ),
        ToolExample(
            utterance="create a ppt called Q3 Review with a light theme",
            arguments={"title": "Q3 Review", "theme": "light", "format": "pptx"},
        ),
        ToolExample(
            utterance="build a deck from these slides",
            arguments={
                "title": "Launch Plan",
                "slides": [
                    {"title": "Launch Plan", "bullets": ["Our roadmap for Q4"]},
                    {"title": "Milestones", "bullets": ["Beta in October", "GA in December"],
                     "notes": "Emphasize the beta feedback loop."},
                ],
            },
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "title": {"type": "string", "description": "Presentation title (also used for the filename)."},
            "slides": {
                "type": "array",
                "description": "Optional explicit slides: [{title, bullets: [str], notes: str}]. "
                               "When omitted, an outline is generated from 'topic' (or the title).",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "bullets": {"type": "array", "items": {"type": "string"}},
                        "notes": {"type": "string"},
                    },
                },
            },
            "topic": {"type": "string", "description": "Topic to auto-generate an outline from when no slides are given."},
            "theme": {"type": "string", "enum": ["dark", "light"], "description": "Visual theme (default dark)."},
            "format": {
                "type": "string",
                "enum": ["pptx", "html", "auto"],
                "description": "Output format. 'auto' (default) prefers pptx and falls back to an HTML deck.",
            },
        },
        required=["title"],
    )

    async def _run(
        self,
        title: str = "",
        slides: Any = None,
        topic: str = "",
        theme: str = "dark",
        format: str = "auto",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ToolError(
                "A presentation needs a title.",
                speech="I need a title for the presentation.",
            )

        theme = str(theme or "dark").strip().lower()
        if theme not in _HTML_PALETTES:
            theme = "dark"

        fmt = str(format or "auto").strip().lower().lstrip(".")
        fmt = {"ppt": "pptx", "powerpoint": "pptx", "web": "html", "htm": "html"}.get(fmt, fmt)
        if fmt not in ("pptx", "html", "auto"):
            raise ToolError(
                f"Unknown presentation format '{format}'. Use 'pptx', 'html' or 'auto'.",
                speech="I can only make PowerPoint or HTML decks.",
            )

        if slides:
            deck = normalize_slides(slides)
        else:
            deck = generate_outline(str(topic or "").strip() or title)

        pptx_available = try_import("pptx") is not None
        fell_back = False
        if fmt == "pptx" and not pptx_available:
            raise ToolError(
                "python-pptx is not installed, so I can't write .pptx files. "
                "Install it with: pip install python-pptx (or use format='html').",
                speech="PowerPoint support isn't installed, but I can make an HTML deck instead.",
            )
        if fmt == "auto":
            fmt = "pptx" if pptx_available else "html"
            fell_back = fmt == "html"

        path = _deliverable_path(title, fmt)
        if fmt == "pptx":
            await self.to_thread(_write_pptx, path, title, deck, theme)
        else:
            document = build_html_deck(title, deck, theme)
            await self.to_thread(path.write_text, document, encoding="utf-8")

        logger.info("Created %s presentation with %d slides at %s", fmt, len(deck), path)
        speech = f"Created a {len(deck)}-slide presentation called {title}."
        if fell_back:
            speech += " I built it as an HTML deck since PowerPoint support isn't installed."
        return {
            "path": str(path),
            "format": fmt,
            "theme": theme,
            "slide_count": len(deck),
            "slide_titles": [s["title"] for s in deck],
            "fallback_used": fell_back,
            "speech": speech,
            "display": f"Presentation '{title}' ({len(deck)} slides, {fmt.upper()}, {theme} theme)\n{path}",
            "artifacts": [str(path)],
            "ui": {"open": str(path)},
        }


def get_tools() -> list[BaseTool]:
    return [CreatePresentationTool()]
