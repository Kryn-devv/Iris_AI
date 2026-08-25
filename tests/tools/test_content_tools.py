"""Tests for the content-creation tool modules (presentation, documents, spreadsheet).

Strategy:

* Pure helpers (slugify, outline generation, HTML deck builder, markdown-ish
  block parsing, row normalization) are tested directly.
* Fallback writers (HTML deck, .md document, .csv sheet) are forced by
  monkeypatching each module's ``try_import`` to return ``None``, so the
  zero-dependency paths are covered whether or not the optional packages are
  installed.
* When python-pptx / python-docx / openpyxl ARE installed, the rich writers
  are additionally exercised end-to-end and the artifacts re-opened with the
  same libraries.

All files land in a temporary workspace: ``IRIS_WORKSPACE_DIR`` is
monkeypatched and the module-level path sandbox replaced with a permissive
stub (mirroring ``test_windows_screenshot_notify.py``). Nothing touches the
real home directory and no display is required.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from iris.app.core import paths
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory
from iris.app.tools.base import ToolError
from iris.app.tools.content import documents as documents_mod
from iris.app.tools.content import presentation as presentation_mod
from iris.app.tools.content import spreadsheet as spreadsheet_mod
from iris.app.tools.content.documents import (
    QuickNoteTool,
    WriteDocumentTool,
    blocks_to_markdown,
    blocks_to_text,
    parse_blocks,
)
from iris.app.tools.content.presentation import (
    CreatePresentationTool,
    build_html_deck,
    generate_outline,
    normalize_slides,
    slugify,
)
from iris.app.tools.content.spreadsheet import (
    CreateSpreadsheetTool,
    normalize_rows,
    normalize_sheets,
    sanitize_sheet_name,
)


HAS_PPTX = presentation_mod.try_import("pptx") is not None
HAS_DOCX = documents_mod.try_import("docx") is not None
HAS_OPENPYXL = spreadsheet_mod.try_import("openpyxl") is not None

_NO_IMPORTS = lambda name: None  # noqa: E731 - tiny stand-in for try_import


class _OpenSandbox:
    """Permissive sandbox stand-in so tmp_path counts as an allowed root."""

    def resolve(self, raw, *, must_exist: bool = False) -> Path:
        return Path(raw).expanduser().resolve()


@pytest.fixture()
def outputs_tmp(tmp_path, monkeypatch) -> Path:
    """Redirect the IRIS workspace (and thus outputs_dir) into tmp_path."""
    monkeypatch.setenv("IRIS_WORKSPACE_DIR", str(tmp_path))
    for module in (presentation_mod, documents_mod, spreadsheet_mod):
        monkeypatch.setattr(module, "default_path_sandbox", _OpenSandbox())
    assert paths.outputs_dir() == tmp_path / "outputs"
    return tmp_path / "outputs"


# =============================================================================
# Pure helpers: slugify
# =============================================================================

def test_slugify_basic():
    assert slugify("Hello, World! 2026") == "hello-world-2026"
    assert slugify("  Q3 -- Review  ") == "q3-review"


def test_slugify_unicode_folds_to_ascii():
    assert slugify("Café Décor & Möbel") == "cafe-decor-mobel"


def test_slugify_degenerate_inputs():
    assert slugify("") == "untitled"
    assert slugify("!!!***") == "untitled"
    assert slugify(None) == "untitled"


def test_slugify_truncates_long_titles():
    slug = slugify("word " * 50, max_length=20)
    assert len(slug) <= 20
    assert not slug.endswith("-")


# =============================================================================
# Pure helpers: outline generation
# =============================================================================

def test_generate_outline_structure():
    outline = generate_outline("renewable energy")
    assert isinstance(outline, list)
    # title + agenda + 3..5 sections + summary + Q&A
    assert 7 <= len(outline) <= 9
    for slide in outline:
        assert set(slide) == {"title", "bullets", "notes"}
        assert slide["title"]
        assert isinstance(slide["bullets"], list)
        assert slide["notes"]
    assert outline[0]["title"] == "Renewable energy"
    assert outline[1]["title"] == "Agenda"
    assert outline[-2]["title"] == "Summary"
    assert "Q&A" in outline[-1]["title"]


def test_generate_outline_is_deterministic():
    assert generate_outline("machine learning") == generate_outline("machine learning")


def test_generate_outline_section_count_scales_with_keywords():
    short = generate_outline("tea")
    long = generate_outline("distributed database replication consistency tuning")
    assert len(short) == 7   # 3 content sections minimum
    assert len(long) == 9    # capped at 5 content sections


def test_generate_outline_handles_empty_topic():
    outline = generate_outline("")
    assert outline[0]["title"] == "Untitled Topic"
    assert len(outline) >= 7


def test_generate_outline_agenda_lists_sections():
    outline = generate_outline("home automation")
    section_titles = [s["title"] for s in outline[2:-2]]
    for title in section_titles:
        assert title in outline[1]["bullets"]


# =============================================================================
# Pure helpers: slide normalization
# =============================================================================

def test_normalize_slides_coerces_strings_and_dicts():
    slides = normalize_slides(["Intro", {"title": "Data", "bullets": ["a", " ", "b"], "notes": "hi"}])
    assert slides[0] == {"title": "Intro", "bullets": [], "notes": ""}
    assert slides[1] == {"title": "Data", "bullets": ["a", "b"], "notes": "hi"}


def test_normalize_slides_rejects_bad_shapes():
    with pytest.raises(ToolError):
        normalize_slides("not-a-list")
    with pytest.raises(ToolError):
        normalize_slides([42])
    with pytest.raises(ToolError):
        normalize_slides([])


# =============================================================================
# Pure helpers: HTML deck builder
# =============================================================================

def test_build_html_deck_contains_slides_and_keyboard_js():
    slides = [
        {"title": "My Deck", "bullets": ["welcome"], "notes": "hello"},
        {"title": "Second Slide", "bullets": ["one", "two"], "notes": ""},
        {"title": "Third", "bullets": [], "notes": "wrap up"},
    ]
    html = build_html_deck("My Deck", slides, theme="dark")
    assert html.count('<section class="slide') == 3
    assert "My Deck" in html and "Second Slide" in html and "Third" in html
    # Keyboard navigation JS
    assert "keydown" in html
    assert "ArrowRight" in html and "ArrowLeft" in html
    # Click navigation and branding footer
    assert 'addEventListener("click"' in html
    assert "Made with" in html and "IRIS" in html
    # Speaker notes are embedded, dark palette applied, fully self-contained
    assert "wrap up" in html
    assert "#0B1020" in html
    assert "http://" not in html and "https://" not in html


def test_build_html_deck_escapes_content():
    html = build_html_deck(
        "<script>alert(1)</script>",
        [{"title": "A & B", "bullets": ["<img src=x>"], "notes": ""}],
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "A &amp; B" in html
    assert "&lt;img src=x&gt;" in html


def test_build_html_deck_light_theme_palette():
    html = build_html_deck("T", [{"title": "T", "bullets": [], "notes": ""}], theme="light")
    assert "#F8FAFC" in html and "#0F766E" in html


# =============================================================================
# CreatePresentationTool
# =============================================================================

async def test_create_presentation_auto_falls_back_to_html(outputs_tmp, monkeypatch):
    monkeypatch.setattr(presentation_mod, "try_import", _NO_IMPORTS)
    tool = CreatePresentationTool()
    result = await tool.execute(title="Solar Power 101", topic="solar power")
    assert result.success, result.error
    assert result.artifacts, "expected an artifact path"
    path = Path(result.artifacts[0])
    assert path.exists()
    assert path.parent == outputs_tmp
    assert path.name.startswith("solar-power-101-")
    assert path.suffix == ".html"
    content = path.read_text(encoding="utf-8")
    assert "Solar Power 101" in content
    assert "keydown" in content and "Made with" in content
    assert result.result["format"] == "html"
    assert result.result["fallback_used"] is True
    assert "HTML" in (result.speech or "")
    assert result.ui == {"open": str(path)}
    assert result.result["slide_count"] >= 7


async def test_create_presentation_with_explicit_slides(outputs_tmp):
    tool = CreatePresentationTool()
    slides = [
        {"title": "Launch Plan", "bullets": ["Q4 roadmap"], "notes": "opener"},
        {"title": "Milestones", "bullets": ["Beta in October", "GA in December"]},
    ]
    result = await tool.execute(title="Launch Plan", slides=slides, format="html", theme="light")
    assert result.success, result.error
    assert result.result["slide_count"] == 2
    assert result.result["slide_titles"] == ["Launch Plan", "Milestones"]
    content = Path(result.artifacts[0]).read_text(encoding="utf-8")
    assert "GA in December" in content
    assert "opener" in content  # speaker notes embedded


@pytest.mark.skipif(not HAS_PPTX, reason="python-pptx not installed")
async def test_create_presentation_real_pptx_roundtrip(outputs_tmp):
    pptx_mod = presentation_mod.try_import("pptx")
    tool = CreatePresentationTool()
    slides = [
        {"title": "Kickoff", "bullets": ["welcome everyone"], "notes": "smile"},
        {"title": "Plan", "bullets": ["step one", "step two"], "notes": "go slow"},
    ]
    result = await tool.execute(title="Kickoff Deck", slides=slides, format="pptx")
    assert result.success, result.error
    path = Path(result.artifacts[0])
    assert path.suffix == ".pptx" and path.parent == outputs_tmp
    deck = pptx_mod.Presentation(str(path))
    assert len(deck.slides) == 2
    # 16:9 aspect ratio
    assert abs((deck.slide_width / deck.slide_height) - (16 / 9)) < 0.01
    # Speaker notes survived the write
    notes = list(deck.slides)[1].notes_slide.notes_text_frame.text
    assert "go slow" in notes


async def test_create_presentation_pptx_unavailable_fails_helpfully(outputs_tmp, monkeypatch):
    monkeypatch.setattr(presentation_mod, "try_import", _NO_IMPORTS)
    result = await CreatePresentationTool().execute(title="Needs PowerPoint", format="pptx")
    assert not result.success
    assert "python-pptx" in (result.error or "")
    assert result.speech


async def test_create_presentation_requires_title(outputs_tmp):
    result = await CreatePresentationTool().execute(title="   ")
    assert not result.success
    assert "title" in (result.error or "").lower()


async def test_create_presentation_rejects_unknown_format(outputs_tmp):
    result = await CreatePresentationTool().execute(title="X", format="keynote")
    assert not result.success
    assert "format" in (result.error or "").lower()


# =============================================================================
# Pure helpers: parse_blocks
# =============================================================================

def test_parse_blocks_headings_bullets_paragraphs():
    text = (
        "# Mission\n"
        "We ship great software\n"
        "every single week.\n"
        "\n"
        "## Values\n"
        "- Ownership\n"
        "* Kindness\n"
        "  - Deep kindness\n"
        "1. Numbered too\n"
        "\n"
        "Closing paragraph."
    )
    blocks = parse_blocks(text)
    assert blocks[0] == {"type": "heading", "level": 1, "text": "Mission"}
    assert blocks[1] == {
        "type": "paragraph", "level": 0,
        "text": "We ship great software every single week.",
    }
    assert blocks[2] == {"type": "heading", "level": 2, "text": "Values"}
    assert blocks[3] == {"type": "bullet", "level": 1, "text": "Ownership"}
    assert blocks[4] == {"type": "bullet", "level": 1, "text": "Kindness"}
    assert blocks[5] == {"type": "bullet", "level": 2, "text": "Deep kindness"}
    assert blocks[6] == {"type": "bullet", "level": 1, "text": "Numbered too"}
    assert blocks[7] == {"type": "paragraph", "level": 0, "text": "Closing paragraph."}


def test_parse_blocks_edge_cases():
    assert parse_blocks("") == []
    assert parse_blocks("   \n\n  ") == []
    # A hash without a following space is a plain paragraph, not a heading.
    assert parse_blocks("#hashtag")[0]["type"] == "paragraph"
    # Hyphen without trailing space is not a bullet.
    assert parse_blocks("-notabullet")[0]["type"] == "paragraph"


def test_blocks_roundtrip_renderers():
    blocks = parse_blocks("# H\n- a\n\npara")
    md = blocks_to_markdown("Title", blocks)
    assert md.startswith("# Title\n")
    assert "## H" in md          # content headings shift below the H1 title
    assert "- a" in md and "para" in md
    txt = blocks_to_text("Title", blocks)
    assert txt.startswith("Title\n=====")
    assert "- a" in txt and "para" in txt


# =============================================================================
# WriteDocumentTool
# =============================================================================

async def test_write_document_auto_falls_back_to_markdown(outputs_tmp, monkeypatch):
    monkeypatch.setattr(documents_mod, "try_import", _NO_IMPORTS)
    tool = WriteDocumentTool()
    result = await tool.execute(
        title="Team Charter",
        content="# Mission\nShip it.\n\n- Ownership\n- Kindness",
    )
    assert result.success, result.error
    path = Path(result.artifacts[0])
    assert path.exists() and path.parent == outputs_tmp
    assert path.name.startswith("team-charter-")
    assert path.suffix == ".md"
    assert result.result["format"] == "md"
    assert result.result["fallback_used"] is True
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# Team Charter")
    assert "## Mission" in content
    assert "- Ownership" in content
    assert result.ui == {"open": str(path)}


async def test_write_document_txt_format(outputs_tmp):
    result = await WriteDocumentTool().execute(
        title="Minutes", content="- Reviewed roadmap\n- Agreed on goals", format="txt"
    )
    assert result.success, result.error
    path = Path(result.artifacts[0])
    assert path.suffix == ".txt"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("Minutes\n")
    assert "Reviewed roadmap" in text


@pytest.mark.skipif(not HAS_DOCX, reason="python-docx not installed")
async def test_write_document_real_docx_roundtrip(outputs_tmp):
    docx_mod = documents_mod.try_import("docx")
    result = await WriteDocumentTool().execute(
        title="Charter",
        content="# Mission\nShip it weekly.\n\n- Ownership",
        format="docx",
    )
    assert result.success, result.error
    path = Path(result.artifacts[0])
    assert path.suffix == ".docx"
    document = docx_mod.Document(str(path))
    texts = [p.text for p in document.paragraphs]
    assert "Charter" in texts and "Mission" in texts and "Ownership" in texts
    styles = {p.text: p.style.name for p in document.paragraphs}
    assert styles["Ownership"] == "List Bullet"


async def test_write_document_docx_unavailable_fails_helpfully(outputs_tmp, monkeypatch):
    monkeypatch.setattr(documents_mod, "try_import", _NO_IMPORTS)
    result = await WriteDocumentTool().execute(title="Letter", content="Dear...", format="docx")
    assert not result.success
    assert "python-docx" in (result.error or "")


async def test_write_document_requires_title_and_content(outputs_tmp):
    assert not (await WriteDocumentTool().execute(title="", content="body")).success
    assert not (await WriteDocumentTool().execute(title="T", content="   ")).success


# =============================================================================
# QuickNoteTool
# =============================================================================

async def test_quick_note_appends_timestamped_lines(outputs_tmp):
    tool = QuickNoteTool()
    first = await tool.execute(text="Call the dentist on Friday")
    assert first.success, first.error
    notes_path = outputs_tmp / "notes.md"
    assert Path(first.artifacts[0]) == notes_path
    assert notes_path.exists()

    second = await tool.execute(text="Wifi password is on the router")
    assert second.success, second.error
    content = notes_path.read_text(encoding="utf-8")
    assert content.startswith("# IRIS Quick Notes")
    assert content.count("# IRIS Quick Notes") == 1  # header written only once
    entries = [line for line in content.splitlines() if line.startswith("- **")]
    assert len(entries) == 2
    assert "Call the dentist on Friday" in entries[0]
    assert "Wifi password is on the router" in entries[1]
    assert second.result["total_notes"] == 2
    assert second.speech == "Noted."


async def test_quick_note_rejects_empty_text(outputs_tmp):
    result = await QuickNoteTool().execute(text="   ")
    assert not result.success


def test_quick_note_metadata():
    tool = QuickNoteTool()
    assert tool.permission_level == PermissionLevel.LOW_RISK_ACTION
    assert tool.mutating is True
    assert set(tool.aliases) == {"note", "take_note", "remember_note"}


# =============================================================================
# Pure helpers: spreadsheet normalization
# =============================================================================

def test_normalize_rows_pads_and_truncates_with_warnings():
    headers, rows, warnings = normalize_rows(
        ["A", "B", "C"],
        [[1, 2, 3], [1], [1, 2, 3, 4], "scalar"],
    )
    assert headers == ["A", "B", "C"]
    assert rows == [[1, 2, 3], [1, "", ""], [1, 2, 3], ["scalar", "", ""]]
    assert len(warnings) == 3
    assert any("padded" in w for w in warnings)
    assert any("truncated" in w for w in warnings)


def test_normalize_rows_maps_dict_rows_onto_headers():
    _, rows, warnings = normalize_rows(
        ["Name", "Role"],
        [{"Role": "Engineer", "Name": "Asha"}, {"Name": "Diego"}],
    )
    assert rows == [["Asha", "Engineer"], ["Diego", ""]]
    assert warnings == []


def test_normalize_rows_dict_without_headers_is_an_error():
    with pytest.raises(ToolError):
        normalize_rows([], [{"a": 1}])


def test_normalize_rows_infers_width_without_headers():
    headers, rows, warnings = normalize_rows(None, [[1, 2], [3]])
    assert headers == []
    assert rows == [[1, 2], [3, ""]]
    assert len(warnings) == 1


def test_sanitize_sheet_name():
    assert sanitize_sheet_name("Budget [2026] / Q3*?") == "Budget 2026 Q3"
    assert sanitize_sheet_name("") == "Sheet1"
    assert len(sanitize_sheet_name("x" * 99)) == 31


def test_normalize_sheets_dedupes_names():
    specs, _ = normalize_sheets(
        "T", None, None,
        [{"name": "Data", "rows": [[1]]}, {"name": "data", "rows": [[2]]}],
    )
    names = [spec["name"] for spec in specs]
    assert len(set(names)) == 2
    assert names[0] == "Data"


# =============================================================================
# CreateSpreadsheetTool
# =============================================================================

async def test_create_spreadsheet_csv_writes_real_file(outputs_tmp):
    tool = CreateSpreadsheetTool()
    result = await tool.execute(
        title="Team Roster",
        headers=["Name", "Role"],
        rows=[["Asha", "Engineer"], ["Diego"]],  # short row gets padded
        format="csv",
    )
    assert result.success, result.error
    path = Path(result.artifacts[0])
    assert path.exists() and path.parent == outputs_tmp
    assert path.suffix == ".csv"
    assert path.name.startswith("team-roster-")
    with path.open(newline="", encoding="utf-8") as handle:
        parsed = list(csv.reader(handle))
    assert parsed == [["Name", "Role"], ["Asha", "Engineer"], ["Diego", ""]]
    assert result.result["rows_written"] == 2
    assert any("padded" in w for w in result.result["warnings"])
    assert result.ui == {"open": str(path)}


async def test_create_spreadsheet_auto_falls_back_to_csv(outputs_tmp, monkeypatch):
    monkeypatch.setattr(spreadsheet_mod, "try_import", _NO_IMPORTS)
    result = await CreateSpreadsheetTool().execute(
        title="Expenses", headers=["Item", "Amount"], rows=[["Coffee", 4.5]]
    )
    assert result.success, result.error
    path = Path(result.artifacts[0])
    assert path.suffix == ".csv"
    assert result.result["fallback_used"] is True
    assert "CSV" in (result.speech or "")


@pytest.mark.skipif(not HAS_OPENPYXL, reason="openpyxl not installed")
async def test_create_spreadsheet_real_xlsx_roundtrip(outputs_tmp):
    openpyxl_mod = spreadsheet_mod.try_import("openpyxl")
    result = await CreateSpreadsheetTool().execute(
        title="Q3 Finances",
        sheets=[
            {"name": "Budget", "headers": ["Category", "Planned"], "rows": [["Ads", 1200]]},
            {"name": "Actuals", "headers": ["Category", "Spent"], "rows": [["Ads", 980]]},
        ],
        format="xlsx",
    )
    assert result.success, result.error
    path = Path(result.artifacts[0])
    assert path.suffix == ".xlsx"
    workbook = openpyxl_mod.load_workbook(str(path))
    assert workbook.sheetnames == ["Budget", "Actuals"]
    budget = workbook["Budget"]
    assert budget["A1"].value == "Category"
    assert budget["A1"].font.bold is True
    assert str(budget.freeze_panes) == "A2"
    assert budget["B2"].value == 1200
    assert result.result["sheets"] == ["Budget", "Actuals"]


async def test_create_spreadsheet_xlsx_unavailable_fails_helpfully(outputs_tmp, monkeypatch):
    monkeypatch.setattr(spreadsheet_mod, "try_import", _NO_IMPORTS)
    result = await CreateSpreadsheetTool().execute(
        title="Budget", headers=["A"], rows=[[1]], format="xlsx"
    )
    assert not result.success
    assert "openpyxl" in (result.error or "")


async def test_create_spreadsheet_multi_sheet_csv_keeps_first_sheet(outputs_tmp):
    result = await CreateSpreadsheetTool().execute(
        title="Q3 Finances",
        sheets=[
            {"name": "Budget", "headers": ["Category", "Planned"], "rows": [["Ads", 1200]]},
            {"name": "Actuals", "headers": ["Category", "Spent"], "rows": [["Ads", 980]]},
        ],
        format="csv",
    )
    assert result.success, result.error
    assert result.result["sheets"] == ["Budget"]
    assert any("single sheet" in w for w in result.result["warnings"])
    with Path(result.artifacts[0]).open(newline="", encoding="utf-8") as handle:
        parsed = list(csv.reader(handle))
    assert parsed == [["Category", "Planned"], ["Ads", "1200"]]


async def test_create_spreadsheet_requires_title(outputs_tmp):
    result = await CreateSpreadsheetTool().execute(title="", rows=[[1]])
    assert not result.success


# =============================================================================
# Metadata & module factories
# =============================================================================

@pytest.mark.parametrize(
    ("module", "expected_names"),
    [
        (presentation_mod, {"create_presentation"}),
        (documents_mod, {"write_document", "quick_note"}),
        (spreadsheet_mod, {"create_spreadsheet"}),
    ],
)
def test_get_tools_factories(module, expected_names):
    tools = module.get_tools()
    assert {tool.name for tool in tools} == expected_names
    for tool in tools:
        assert tool.category == ToolCategory.CONTENT
        assert tool.mutating is True
        assert tool.description
        assert tool.examples
        assert tool.input_schema.required
        metadata = tool.get_metadata()
        assert metadata.available is True  # fallbacks keep everything usable


def test_write_tool_metadata():
    for tool_cls, aliases in (
        (CreatePresentationTool,
         {"make_ppt", "create_ppt", "make_presentation", "make_slides", "create_deck"}),
        (WriteDocumentTool,
         {"create_doc", "make_document", "write_letter", "write_essay", "create_word_doc"}),
        (CreateSpreadsheetTool, {"make_excel", "create_excel", "make_sheet", "create_csv"}),
    ):
        tool = tool_cls()
        assert tool.permission_level == PermissionLevel.WRITE_ACTION
        assert set(tool.aliases) == aliases
        assert tool.network is False
