"""Document creation tools for IRIS.

* :class:`WriteDocumentTool` — turns markdown-ish text (``#`` headings,
  ``-`` bullets, plain paragraphs) into a Word document via ``python-docx``
  when available, falling back to a clean ``.md`` file (or plain ``.txt`` on
  request) with zero dependencies.
* :class:`QuickNoteTool` — appends a timestamped line to a rolling
  ``notes.md`` in the outputs directory, for "remember this" moments.

``parse_blocks`` is a tiny pure markdown-ish parser shared by every renderer
so the same content produces the same structure in every format.
"""

from __future__ import annotations

import datetime
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

logger = get_logger("tools.content.documents")


# =============================================================================
# Pure helpers
# =============================================================================

def _slugify(text: str, *, max_length: int = 60) -> str:
    """Filesystem-safe lowercase slug (local copy — modules stay self-contained)."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_length].rstrip("-") or "untitled"


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^(\s*)(?:[-*+•]|\d{1,3}[.)])\s+(.+?)\s*$")


def parse_blocks(text: str) -> List[Dict[str, Any]]:
    """Parse markdown-ish text into a flat list of typed blocks.

    Returns dictionaries shaped ``{"type": ..., "level": ..., "text": ...}``:

    * ``heading`` — ``#``-prefixed lines; ``level`` is the number of hashes.
    * ``bullet`` — ``-``/``*``/``+``/``•`` or numbered (``1.``/``1)``) lines;
      ``level`` starts at 1 and grows with two-space indentation (capped at 3).
    * ``paragraph`` — consecutive plain lines merged into one block
      (``level`` 0); blank lines separate paragraphs.
    """
    blocks: List[Dict[str, Any]] = []
    paragraph: List[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append({"type": "paragraph", "level": 0, "text": " ".join(paragraph)})
            paragraph.clear()

    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush()
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush()
            blocks.append({"type": "heading", "level": len(heading.group(1)),
                           "text": heading.group(2).strip()})
            continue
        bullet = _BULLET_RE.match(raw_line.rstrip())
        if bullet:
            flush()
            indent = len(bullet.group(1).replace("\t", "  "))
            blocks.append({"type": "bullet", "level": min(3, indent // 2 + 1),
                           "text": bullet.group(2).strip()})
            continue
        paragraph.append(stripped)
    flush()
    return blocks


def blocks_to_markdown(title: str, blocks: List[Dict[str, Any]]) -> str:
    """Render parsed blocks back into clean markdown, prefixed with the title."""
    lines: List[str] = [f"# {title}", ""]
    previous = ""
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            if previous:
                lines.append("")
            # The document title is H1, so content headings shift one level down.
            level = min(6, max(2, block["level"] + 1))
            lines.append(f"{'#' * level} {block['text']}")
            lines.append("")
        elif kind == "bullet":
            if previous == "paragraph":
                lines.append("")
            lines.append(f"{'  ' * (block['level'] - 1)}- {block['text']}")
        else:
            if previous:
                lines.append("")
            lines.append(block["text"])
        previous = kind
    return "\n".join(lines).rstrip() + "\n"


def blocks_to_text(title: str, blocks: List[Dict[str, Any]]) -> str:
    """Render parsed blocks as tidy plain text with an underlined title."""
    lines: List[str] = [title, "=" * max(4, len(title)), ""]
    previous = ""
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            if previous:
                lines.append("")
            lines.append(block["text"])
            lines.append("-" * max(4, len(block["text"])))
        elif kind == "bullet":
            if previous == "paragraph":
                lines.append("")
            lines.append(f"{'  ' * (block['level'] - 1)}  - {block['text']}")
        else:
            if previous:
                lines.append("")
            lines.append(block["text"])
        previous = kind
    return "\n".join(lines).rstrip() + "\n"


def _write_docx(path: Path, title: str, blocks: List[Dict[str, Any]]) -> None:
    """Write a Word document using python-docx (availability checked by caller)."""
    docx_mod = try_import("docx")
    if docx_mod is None:
        raise ToolError(
            "python-docx is not installed. Install it with: pip install python-docx",
            speech="I need the python-docx package to write Word documents.",
        )
    document = docx_mod.Document()
    document.add_heading(title, level=0)
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            document.add_heading(block["text"], level=min(4, max(1, block["level"])))
        elif kind == "bullet":
            level = min(3, max(1, block["level"]))
            style = "List Bullet" if level == 1 else f"List Bullet {level}"
            try:
                document.add_paragraph(block["text"], style=style)
            except KeyError:  # template without nested bullet styles
                document.add_paragraph(block["text"], style="List Bullet")
        else:
            document.add_paragraph(block["text"])
    document.save(str(path))


def _deliverable_path(title: str, extension: str, *, filename: str | None = None) -> Path:
    """Unique, sandbox-validated output path inside ``paths.outputs_dir()``."""
    out_dir = paths.outputs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{_slugify(title)}-{stamp}"
        candidate = out_dir / f"{base}.{extension}"
        counter = 2
        while candidate.exists():
            candidate = out_dir / f"{base}-{counter}.{extension}"
            counter += 1
    else:
        candidate = out_dir / filename
    try:
        return default_path_sandbox.resolve(candidate)
    except SandboxError as exc:
        raise ToolError(str(exc), speech="I couldn't write inside the allowed workspace.") from exc


# =============================================================================
# Tools
# =============================================================================

class WriteDocumentTool(BaseTool):
    """Write a document from markdown-ish content: .docx when python-docx is
    installed, with .md and .txt always available as zero-dependency formats."""

    name = "write_document"
    description = "Writes a document (Word, Markdown or plain text) from a title and markdown-ish content."
    permission_level = PermissionLevel.WRITE_ACTION
    category = ToolCategory.CONTENT
    aliases = ("create_doc", "make_document", "write_letter", "write_essay", "create_word_doc")
    mutating = True
    examples = (
        ToolExample(
            utterance="write a document titled Team Charter",
            arguments={
                "title": "Team Charter",
                "content": "# Mission\nShip great software.\n\n# Values\n- Ownership\n- Kindness",
            },
        ),
        ToolExample(
            utterance="write a letter to my landlord as a word doc",
            arguments={
                "title": "Letter to Landlord",
                "content": "Dear Sir or Madam,\n\nI am writing about the heating issue in flat 4B.",
                "format": "docx",
            },
        ),
        ToolExample(
            utterance="save this as a plain text file called Meeting Minutes",
            arguments={"title": "Meeting Minutes", "content": "- Reviewed roadmap\n- Agreed on Q4 goals",
                       "format": "txt"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "title": {"type": "string", "description": "Document title (also used for the filename)."},
            "content": {
                "type": "string",
                "description": "Markdown-ish body: '#' headings, '-' bullets and plain paragraphs.",
            },
            "format": {
                "type": "string",
                "enum": ["docx", "md", "txt", "auto"],
                "description": "Output format. 'auto' (default) prefers docx and falls back to markdown.",
            },
        },
        required=["title", "content"],
    )

    async def _run(
        self,
        title: str = "",
        content: str = "",
        format: str = "auto",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ToolError("A document needs a title.", speech="I need a title for the document.")
        content = str(content or "")
        if not content.strip():
            raise ToolError(
                "There was no content to write into the document.",
                speech="I need some content for the document.",
            )

        fmt = str(format or "auto").strip().lower().lstrip(".")
        fmt = {"doc": "docx", "word": "docx", "markdown": "md", "text": "txt"}.get(fmt, fmt)
        if fmt not in ("docx", "md", "txt", "auto"):
            raise ToolError(
                f"Unknown document format '{format}'. Use 'docx', 'md', 'txt' or 'auto'.",
                speech="I can only write Word, markdown or plain text documents.",
            )

        blocks = parse_blocks(content)
        docx_available = try_import("docx") is not None
        fell_back = False
        if fmt == "docx" and not docx_available:
            raise ToolError(
                "python-docx is not installed, so I can't write .docx files. "
                "Install it with: pip install python-docx (or use format='md').",
                speech="Word support isn't installed, but I can write a markdown file instead.",
            )
        if fmt == "auto":
            fmt = "docx" if docx_available else "md"
            fell_back = fmt == "md"

        path = _deliverable_path(title, fmt)
        if fmt == "docx":
            await self.to_thread(_write_docx, path, title, blocks)
        elif fmt == "md":
            await self.to_thread(
                path.write_text, blocks_to_markdown(title, blocks), encoding="utf-8"
            )
        else:
            await self.to_thread(
                path.write_text, blocks_to_text(title, blocks), encoding="utf-8"
            )

        word_count = len(re.findall(r"\S+", content))
        logger.info("Wrote %s document '%s' (%d blocks) to %s", fmt, title, len(blocks), path)
        speech = f"Wrote the document {title}."
        if fell_back:
            speech += " I saved it as markdown since Word support isn't installed."
        return {
            "path": str(path),
            "format": fmt,
            "block_count": len(blocks),
            "word_count": word_count,
            "fallback_used": fell_back,
            "speech": speech,
            "display": f"Document '{title}' ({fmt.upper()}, {word_count} words)\n{path}",
            "artifacts": [str(path)],
            "ui": {"open": str(path)},
        }


class QuickNoteTool(BaseTool):
    """Append a timestamped quick note to a rolling notes.md in the outputs
    directory — a friction-free 'remember this for me' capture."""

    name = "quick_note"
    description = "Appends a timestamped quick note to the rolling notes.md file in the outputs folder."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.CONTENT
    aliases = ("note", "take_note", "remember_note")
    mutating = True
    examples = (
        ToolExample(
            utterance="note that the wifi password is on the router",
            arguments={"text": "The wifi password is on the router"},
        ),
        ToolExample(
            utterance="take a note: call the dentist on Friday",
            arguments={"text": "Call the dentist on Friday"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "text": {"type": "string", "description": "The note text to remember."},
        },
        required=["text"],
    )

    #: Filename of the rolling notes file inside ``paths.outputs_dir()``.
    NOTES_FILENAME = "notes.md"

    async def _run(self, text: str = "", **kwargs: Any) -> Dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ToolError("The note was empty.", speech="I need some text to note down.")

        path = _deliverable_path("notes", "md", filename=self.NOTES_FILENAME)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        body = "\n  ".join(line.strip() for line in text.splitlines() if line.strip())
        entry = f"- **{stamp}** {body}\n"

        def _append() -> int:
            is_new = not path.exists()
            with path.open("a", encoding="utf-8") as handle:
                if is_new:
                    handle.write("# IRIS Quick Notes\n\n")
                handle.write(entry)
            content = path.read_text(encoding="utf-8")
            return sum(1 for line in content.splitlines() if line.startswith("- **"))

        total = await self.to_thread(_append)
        logger.info("Appended a quick note to %s (%d total)", path, total)
        return {
            "path": str(path),
            "note": text,
            "timestamp": stamp,
            "total_notes": total,
            "speech": "Noted.",
            "display": f"Noted at {stamp}: {text}\n({total} notes in {path})",
            "artifacts": [str(path)],
            "ui": {"open": str(path)},
        }


def get_tools() -> list[BaseTool]:
    return [WriteDocumentTool(), QuickNoteTool()]
