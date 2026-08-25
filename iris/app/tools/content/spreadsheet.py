"""Spreadsheet creation tools for IRIS.

:class:`CreateSpreadsheetTool` writes tabular data to a styled ``.xlsx``
workbook when ``openpyxl`` is installed (bold white-on-dark header row, frozen
pane, auto column widths, multi-sheet support) and falls back to a
single-sheet ``.csv`` written with the stdlib ``csv`` module otherwise.

Row widths are validated against the headers: short rows are padded, long
rows truncated, and every adjustment is reported in a ``warnings`` field so
callers can see exactly what was fixed up.
"""

from __future__ import annotations

import csv
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

from iris.app.core import paths
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import try_import
from iris.app.core.security import PermissionLevel, SandboxError, default_path_sandbox
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.content.spreadsheet")


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


#: Characters Excel forbids in worksheet names.
_SHEET_NAME_BAD = re.compile(r"[\[\]:*?/\\]")


def sanitize_sheet_name(name: Any, fallback: str = "Sheet1") -> str:
    """Make a worksheet name Excel will accept: no ``[]:*?/\\``, max 31 chars."""
    clean = _SHEET_NAME_BAD.sub(" ", str(name or "")).strip().strip("'")
    clean = re.sub(r"\s+", " ", clean)
    return clean[:31].strip() or fallback


def normalize_rows(
    headers: Any, rows: Any, *, sheet_label: str = ""
) -> Tuple[List[str], List[List[Any]], List[str]]:
    """Validate and normalize tabular data against the header row.

    * Headers are coerced to strings.
    * Rows may be lists/tuples, dicts keyed by header, or bare scalars
      (treated as single-cell rows).
    * Rows shorter than the header row are padded with empty strings; longer
      rows are truncated. Every adjustment produces a human-readable warning.

    Returns ``(headers, rows, warnings)``. Pure function — safe to unit test.
    """
    prefix = f"Sheet '{sheet_label}': " if sheet_label else ""
    warnings: List[str] = []
    norm_headers = [str(h) for h in (headers or [])]
    raw_rows = list(rows or [])

    width = len(norm_headers)
    if not width and raw_rows:
        width = max(
            len(row) if isinstance(row, (list, tuple)) else 1 for row in raw_rows
        )

    norm_rows: List[List[Any]] = []
    for index, row in enumerate(raw_rows, start=1):
        if isinstance(row, (list, tuple)):
            cells = list(row)
        elif isinstance(row, dict):
            if not norm_headers:
                raise ToolError(
                    f"{prefix}Row {index} is an object but there are no headers to map it onto.",
                    speech="I need headers to place that row's values.",
                )
            cells = [row.get(header, "") for header in norm_headers]
        else:
            cells = [row]

        if width and len(cells) < width:
            warnings.append(
                f"{prefix}Row {index} had {len(cells)} value(s); padded to {width} columns."
            )
            cells = cells + [""] * (width - len(cells))
        elif width and len(cells) > width:
            warnings.append(
                f"{prefix}Row {index} had {len(cells)} value(s); truncated to {width} columns."
            )
            cells = cells[:width]
        norm_rows.append(cells)

    return norm_headers, norm_rows, warnings


def normalize_sheets(
    title: str, headers: Any, rows: Any, sheets: Any
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Build canonical sheet specs from either flat args or a 'sheets' list.

    Returns ``(sheet_specs, warnings)`` where each spec is
    ``{"name": str, "headers": [str], "rows": [[Any]]}`` with unique,
    Excel-safe names. Pure function.
    """
    warnings: List[str] = []
    specs: List[Dict[str, Any]] = []

    if sheets:
        if not isinstance(sheets, (list, tuple)):
            raise ToolError(
                "The 'sheets' argument must be a list of {name, headers, rows} objects.",
                speech="The sheets you gave me were in the wrong shape.",
            )
        for index, sheet in enumerate(sheets, start=1):
            if not isinstance(sheet, dict):
                raise ToolError(
                    f"Sheet {index} must be an object with name/headers/rows.",
                    speech=f"Sheet {index} was in the wrong shape.",
                )
            name = sanitize_sheet_name(sheet.get("name"), fallback=f"Sheet{index}")
            sheet_headers, sheet_rows, sheet_warnings = normalize_rows(
                sheet.get("headers"), sheet.get("rows"), sheet_label=name
            )
            warnings.extend(sheet_warnings)
            specs.append({"name": name, "headers": sheet_headers, "rows": sheet_rows})
    else:
        name = sanitize_sheet_name(title, fallback="Sheet1")
        sheet_headers, sheet_rows, sheet_warnings = normalize_rows(headers, rows)
        warnings.extend(sheet_warnings)
        specs.append({"name": name, "headers": sheet_headers, "rows": sheet_rows})

    # De-duplicate worksheet names (Excel requires uniqueness).
    seen: Dict[str, int] = {}
    for spec in specs:
        base = spec["name"]
        key = base.lower()
        if key in seen:
            seen[key] += 1
            spec["name"] = sanitize_sheet_name(f"{base[:27]} ({seen[key]})")
        else:
            seen[key] = 1

    return specs, warnings


# =============================================================================
# Writers
# =============================================================================

def _write_xlsx(path: Path, sheets: List[Dict[str, Any]]) -> None:
    """Write a styled multi-sheet workbook (openpyxl checked by caller)."""
    openpyxl_mod = try_import("openpyxl")
    styles = try_import("openpyxl.styles")
    utils = try_import("openpyxl.utils")
    if not all((openpyxl_mod, styles, utils)):
        raise ToolError(
            "openpyxl is not installed. Install it with: pip install openpyxl",
            speech="I need the openpyxl package to build Excel files.",
        )

    workbook = openpyxl_mod.Workbook()
    workbook.remove(workbook.active)  # drop the default empty sheet
    header_font = styles.Font(bold=True, color="FFFFFF")
    header_fill = styles.PatternFill(start_color="111827", end_color="111827", fill_type="solid")
    header_alignment = styles.Alignment(vertical="center")

    for spec in sheets:
        worksheet = workbook.create_sheet(title=spec["name"])
        headers: List[str] = spec["headers"]
        rows: List[List[Any]] = spec["rows"]

        first_data_row = 1
        if headers:
            for column, header in enumerate(headers, start=1):
                cell = worksheet.cell(row=1, column=column, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
            worksheet.freeze_panes = "A2"
            first_data_row = 2

        for offset, row in enumerate(rows):
            for column, value in enumerate(row, start=1):
                worksheet.cell(row=first_data_row + offset, column=column, value=value)

        # Auto column widths from the longest value per column.
        column_count = max(len(headers), max((len(r) for r in rows), default=0))
        for column in range(1, column_count + 1):
            longest = len(headers[column - 1]) if column <= len(headers) else 0
            for row in rows:
                if column <= len(row) and row[column - 1] is not None:
                    longest = max(longest, len(str(row[column - 1])))
            letter = utils.get_column_letter(column)
            worksheet.column_dimensions[letter].width = min(60, max(10, longest + 2))

    workbook.save(str(path))


def _write_csv(path: Path, headers: List[str], rows: List[List[Any]]) -> None:
    """Write a single sheet as CSV using only the stdlib."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if headers:
            writer.writerow(headers)
        writer.writerows(rows)


def _deliverable_path(title: str, extension: str) -> Path:
    """Unique, sandbox-validated output path inside ``paths.outputs_dir()``."""
    out_dir = paths.outputs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{_slugify(title)}-{stamp}"
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

class CreateSpreadsheetTool(BaseTool):
    """Create a spreadsheet: a styled .xlsx when openpyxl is installed, or a
    plain single-sheet .csv (stdlib) otherwise."""

    name = "create_spreadsheet"
    description = "Creates a spreadsheet (Excel workbook or CSV) from headers and rows, with multi-sheet support."
    permission_level = PermissionLevel.WRITE_ACTION
    category = ToolCategory.CONTENT
    aliases = ("make_excel", "create_excel", "make_sheet", "create_csv")
    mutating = True
    examples = (
        ToolExample(
            utterance="make an excel sheet of my expenses",
            arguments={
                "title": "Expenses",
                "headers": ["Date", "Item", "Amount"],
                "rows": [["2026-08-01", "Coffee", 4.5], ["2026-08-02", "Groceries", 62.1]],
            },
        ),
        ToolExample(
            utterance="create a csv of the team roster",
            arguments={
                "title": "Team Roster",
                "headers": ["Name", "Role"],
                "rows": [["Asha", "Engineer"], ["Diego", "Designer"]],
                "format": "csv",
            },
        ),
        ToolExample(
            utterance="build a workbook with a budget sheet and an actuals sheet",
            arguments={
                "title": "Q3 Finances",
                "sheets": [
                    {"name": "Budget", "headers": ["Category", "Planned"], "rows": [["Ads", 1200]]},
                    {"name": "Actuals", "headers": ["Category", "Spent"], "rows": [["Ads", 980]]},
                ],
            },
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "title": {"type": "string", "description": "Workbook title (also used for the filename)."},
            "headers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column headers for a single-sheet spreadsheet.",
            },
            "rows": {
                "type": "array",
                "items": {"type": "array"},
                "description": "Data rows (arrays of cell values) for a single-sheet spreadsheet.",
            },
            "sheets": {
                "type": "array",
                "description": "Optional multi-sheet spec: [{name, headers, rows}]. "
                               "Overrides top-level headers/rows when given.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "headers": {"type": "array", "items": {"type": "string"}},
                        "rows": {"type": "array", "items": {"type": "array"}},
                    },
                },
            },
            "format": {
                "type": "string",
                "enum": ["xlsx", "csv", "auto"],
                "description": "Output format. 'auto' (default) prefers xlsx and falls back to CSV.",
            },
        },
        required=["title"],
    )

    async def _run(
        self,
        title: str = "",
        headers: Any = None,
        rows: Any = None,
        sheets: Any = None,
        format: str = "auto",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        title = str(title or "").strip()
        if not title:
            raise ToolError("A spreadsheet needs a title.", speech="I need a title for the spreadsheet.")

        fmt = str(format or "auto").strip().lower().lstrip(".")
        fmt = {"excel": "xlsx", "xls": "xlsx", "sheet": "xlsx"}.get(fmt, fmt)
        if fmt not in ("xlsx", "csv", "auto"):
            raise ToolError(
                f"Unknown spreadsheet format '{format}'. Use 'xlsx', 'csv' or 'auto'.",
                speech="I can only make Excel or CSV files.",
            )

        specs, warnings = normalize_sheets(title, headers, rows, sheets)
        if not any(spec["headers"] or spec["rows"] for spec in specs):
            warnings.append("No headers or rows were provided; the spreadsheet is empty.")

        openpyxl_available = try_import("openpyxl") is not None
        fell_back = False
        if fmt == "xlsx" and not openpyxl_available:
            raise ToolError(
                "openpyxl is not installed, so I can't write .xlsx files. "
                "Install it with: pip install openpyxl (or use format='csv').",
                speech="Excel support isn't installed, but I can make a CSV file instead.",
            )
        if fmt == "auto":
            fmt = "xlsx" if openpyxl_available else "csv"
            fell_back = fmt == "csv"

        if fmt == "csv" and len(specs) > 1:
            skipped = ", ".join(spec["name"] for spec in specs[1:])
            warnings.append(
                f"CSV files hold a single sheet; only '{specs[0]['name']}' was written "
                f"(skipped: {skipped}). Install openpyxl for multi-sheet workbooks."
            )
            specs = specs[:1]

        path = _deliverable_path(title, fmt)
        if fmt == "xlsx":
            await self.to_thread(_write_xlsx, path, specs)
        else:
            await self.to_thread(_write_csv, path, specs[0]["headers"], specs[0]["rows"])

        total_rows = sum(len(spec["rows"]) for spec in specs)
        sheet_names = [spec["name"] for spec in specs]
        logger.info(
            "Created %s spreadsheet '%s' (%d sheet(s), %d row(s)) at %s",
            fmt, title, len(specs), total_rows, path,
        )
        speech = f"Created the spreadsheet {title} with {total_rows} row{'s' if total_rows != 1 else ''}."
        if fell_back:
            speech += " I saved it as a CSV since Excel support isn't installed."
        display_lines = [
            f"Spreadsheet '{title}' ({fmt.upper()}, {len(specs)} sheet(s), {total_rows} rows)",
            str(path),
        ]
        if warnings:
            display_lines.append("Warnings: " + " ".join(warnings))
        return {
            "path": str(path),
            "format": fmt,
            "sheets": sheet_names,
            "rows_written": total_rows,
            "warnings": warnings,
            "fallback_used": fell_back,
            "speech": speech,
            "display": "\n".join(display_lines),
            "artifacts": [str(path)],
            "ui": {"open": str(path)},
        }


def get_tools() -> list[BaseTool]:
    return [CreateSpreadsheetTool()]
