"""Sandboxed file management tools: browse, read, write, copy, move, delete,
search, open and inspect files.

Every path a user or model supplies goes through
``default_path_sandbox.resolve()`` so no tool in this module can touch
anything outside the allow-listed workspace roots, and protected patterns
(SSH keys, ``.env``, credentials) are refused up front. :class:`SandboxError`
is always converted into a clean :class:`ToolError` so the assistant explains
*why* instead of crashing.

Tools:

* :class:`ListDirectoryTool` — "what's in my downloads folder?"
* :class:`ReadFileTool`      — "read notes.txt"
* :class:`WriteFileTool`     — "save this as ideas.md"
* :class:`CopyPathTool`      — "copy report.docx to backup/"
* :class:`MovePathTool`      — "rename draft.md to final.md"
* :class:`DeletePathTool`    — "delete old_logs.txt" (trash when possible)
* :class:`SearchFilesTool`   — "find every markdown file mentioning budget"
* :class:`OpenPathTool`      — "open that PDF" (OS default application)
* :class:`FileInfoTool`      — "how big is that video?"
"""

from __future__ import annotations

import fnmatch
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import current_os, is_windows, is_macos, try_import
from iris.app.core.security import PermissionLevel, SandboxError, default_path_sandbox
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.files.file_manager")

__all__ = [
    "resolve_sandboxed",
    "looks_binary",
    "human_size",
    "ListDirectoryTool",
    "ReadFileTool",
    "WriteFileTool",
    "CopyPathTool",
    "MovePathTool",
    "DeletePathTool",
    "SearchFilesTool",
    "OpenPathTool",
    "FileInfoTool",
    "get_tools",
]

#: Maximum directory entries returned by list_directory.
MAX_LIST_ENTRIES = 500
#: Maximum matches returned by search_files.
MAX_SEARCH_RESULTS = 100
#: Files larger than this are never content-grepped.
MAX_GREP_BYTES = 1_000_000
#: Bytes sniffed from the head of a file for the binary (null byte) check.
_SNIFF_BYTES = 8192


def resolve_sandboxed(raw: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
    """Resolve a path through the sandbox, converting failures to ToolError."""
    try:
        return default_path_sandbox.resolve(raw, must_exist=must_exist)
    except SandboxError as exc:
        raise ToolError(str(exc), speech="That path is outside my allowed workspace.") from exc
    except FileNotFoundError as exc:
        raise ToolError(str(exc), speech="I couldn't find that path.") from exc


def looks_binary(path: Path) -> bool:
    """Cheap binary sniff: a null byte in the first few KB means binary."""
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(_SNIFF_BYTES)
    except OSError:
        return True


def human_size(num: int | float) -> str:
    """Render a byte count as a friendly '1.4 MB' style string."""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"  # pragma: no cover - unreachable


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _is_protected_root(resolved: Path) -> bool:
    """True when ``resolved`` IS an allowed sandbox root or the workspace itself."""
    protected = {root for root in default_path_sandbox.allowed_roots}
    for fn in (paths.workspace_dir, paths.data_dir, paths.outputs_dir, paths.projects_dir):
        try:
            protected.add(fn().resolve())
        except OSError:  # pragma: no cover - defensive
            continue
    protected.add(paths.home_dir().resolve())
    return resolved in protected


# =============================================================================
# Tools
# =============================================================================


class ListDirectoryTool(BaseTool):
    """List a directory's entries with type, size and modification time."""

    name = "list_directory"
    description = "List the files and folders inside a directory."
    permission_level = PermissionLevel.READ
    category = ToolCategory.FILES
    aliases = ("list_files", "show_files", "ls")
    examples = (
        ToolExample(utterance="what's in my workspace?", arguments={}),
        ToolExample(utterance="show the files in projects", arguments={"path": "projects"}),
        ToolExample(
            utterance="list everything in downloads including hidden files",
            arguments={"path": "~/Downloads", "show_hidden": True},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "path": {"type": "string", "description": "Directory to list (default: the IRIS workspace)."},
            "show_hidden": {"type": "boolean", "description": "Include dot-files (default false)."},
        },
        required=[],
    )

    def _list(self, path: str | None, show_hidden: bool) -> dict[str, Any]:
        target = resolve_sandboxed(path or paths.workspace_dir(), must_exist=True)
        if not target.is_dir():
            raise ToolError(f"'{target}' is a file, not a directory.",
                            speech="That's a file, not a folder.")

        entries: list[dict[str, Any]] = []
        truncated = False
        try:
            children = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            raise ToolError(f"Could not list '{target}': {exc}", speech="I couldn't open that folder.") from exc

        for child in children:
            if not show_hidden and child.name.startswith("."):
                continue
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
            try:
                stat = child.stat()
                entry = {
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    "size": 0 if child.is_dir() else stat.st_size,
                    "modified": _iso(stat.st_mtime),
                }
            except OSError:
                entry = {"name": child.name, "type": "unknown", "size": 0, "modified": None}
            entries.append(entry)

        entries.sort(key=lambda e: (e["type"] != "directory", e["name"].lower()))
        return {"path": str(target), "entries": entries, "count": len(entries), "truncated": truncated}

    async def _run(self, path: str | None = None, show_hidden: bool = False, **kwargs: Any) -> dict[str, Any]:
        report = await self.to_thread(self._list, path, bool(show_hidden))
        dirs = sum(1 for e in report["entries"] if e["type"] == "directory")
        files = report["count"] - dirs
        lines = [
            f"{'[dir] ' if e['type'] == 'directory' else '      '}{e['name']}"
            + ("" if e["type"] == "directory" else f"  ({human_size(e['size'])})")
            for e in report["entries"]
        ]
        return {
            **report,
            "speech": f"{Path(report['path']).name or report['path']} has {dirs} folders and {files} files.",
            "display": f"{report['path']}\n" + ("\n".join(lines) if lines else "(empty)"),
        }


class ReadFileTool(BaseTool):
    """Read a text file's contents, refusing binaries and capping the size."""

    name = "read_file"
    description = "Read the text contents of a file."
    permission_level = PermissionLevel.READ
    category = ToolCategory.FILES
    aliases = ("open_file_text", "show_file")
    examples = (
        ToolExample(utterance="read notes.txt", arguments={"path": "notes.txt"}),
        ToolExample(
            utterance="show me the first part of the big log",
            arguments={"path": "logs/app.log", "max_bytes": 20000},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "path": {"type": "string", "description": "File to read."},
            "max_bytes": {
                "type": "integer",
                "description": "Maximum bytes to read (capped by the configured read limit).",
            },
        },
        required=["path"],
    )

    def _read(self, path: str, max_bytes: int | None) -> dict[str, Any]:
        target = resolve_sandboxed(path, must_exist=True)
        if target.is_dir():
            raise ToolError(f"'{target}' is a directory — use list_directory instead.",
                            speech="That's a folder; I can list it instead.")

        limit = settings.FS_MAX_READ_BYTES
        if max_bytes is not None:
            limit = max(1, min(int(max_bytes), settings.FS_MAX_READ_BYTES))

        if looks_binary(target):
            raise ToolError(
                f"'{target.name}' looks like a binary file, so I won't dump it as text. "
                "Use file_info for details about it.",
                speech="That file is binary, so I can't read it as text.",
            )

        size = target.stat().st_size
        with target.open("rb") as fh:
            data = fh.read(limit)
        text = data.decode("utf-8", errors="replace")
        return {
            "path": str(target),
            "text": text,
            "size": size,
            "bytes_read": len(data),
            "truncated": size > len(data),
        }

    async def _run(self, path: str = "", max_bytes: int | None = None, **kwargs: Any) -> dict[str, Any]:
        if not path:
            raise ToolError("A file path is required.", speech="Which file should I read?")
        report = await self.to_thread(self._read, path, max_bytes)
        suffix = " (truncated)" if report["truncated"] else ""
        return {
            **report,
            "speech": f"Read {Path(report['path']).name} — {human_size(report['size'])}{suffix}.",
            "display": report["text"],
        }


class WriteFileTool(BaseTool):
    """Write or append text to a file inside the sandbox, creating parents."""

    name = "write_file"
    description = "Write text content to a file (or append to it)."
    permission_level = PermissionLevel.WRITE_ACTION
    category = ToolCategory.FILES
    aliases = ("save_file",)
    mutating = True
    examples = (
        ToolExample(
            utterance="save this as ideas.md",
            arguments={"path": "ideas.md", "content": "# Ideas\n"},
        ),
        ToolExample(
            utterance="append a line to my journal",
            arguments={"path": "journal.txt", "content": "Went well today.\n", "append": True},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "path": {"type": "string", "description": "Destination file path."},
            "content": {"type": "string", "description": "Text content to write."},
            "append": {"type": "boolean", "description": "Append instead of overwrite (default false)."},
        },
        required=["path", "content"],
    )

    def _write(self, path: str, content: str, append: bool) -> dict[str, Any]:
        target = resolve_sandboxed(path)
        if target.is_dir():
            raise ToolError(f"'{target}' is a directory; give me a file path.",
                            speech="That's a folder, not a file.")
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        return {"path": str(target), "size": target.stat().st_size}

    async def _run(
        self, path: str = "", content: str = "", append: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        if not path:
            raise ToolError("A file path is required.", speech="Where should I save it?")
        encoded = content.encode("utf-8")
        if len(encoded) > settings.FS_MAX_WRITE_BYTES:
            raise ToolError(
                f"The content is {len(encoded)} bytes, above the {settings.FS_MAX_WRITE_BYTES} byte limit.",
                speech="That content is too large for me to write.",
            )
        report = await self.to_thread(self._write, path, content, bool(append))
        verb = "Appended to" if append else "Wrote"
        return {
            **report,
            "bytes_written": len(encoded),
            "appended": bool(append),
            "speech": f"{verb} {Path(report['path']).name}.",
            "artifacts": [report["path"]],
        }


class _TransferTool(BaseTool):
    """Shared copy/move implementation (both are 'source → destination')."""

    _verb = "transfer"
    _past = "transferred"

    def _perform(self, source: Path, destination: Path) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _transfer(self, source: str, destination: str, overwrite: bool) -> dict[str, Any]:
        src = resolve_sandboxed(source, must_exist=True)
        dst = resolve_sandboxed(destination)

        if _is_protected_root(src):
            raise ToolError(f"'{src}' is a protected workspace root and won't be {self._past}.",
                            speech=f"I won't {self._verb} a whole workspace root.")

        # "copy file into existing folder" keeps the original name.
        if dst.is_dir() and (src.is_file() or src.name != dst.name):
            dst = dst / src.name

        if dst == src:
            raise ToolError("Source and destination are the same path.",
                            speech="Those are the same path.")
        if src.is_dir():
            try:
                dst.relative_to(src)
            except ValueError:
                pass
            else:
                raise ToolError(f"Cannot {self._verb} '{src}' into itself.",
                                speech=f"I can't {self._verb} a folder into itself.")
        if dst.exists() and not overwrite:
            raise ToolError(
                f"'{dst}' already exists. Pass overwrite=true to replace it.",
                speech="The destination already exists, so I stopped.",
            )

        dst.parent.mkdir(parents=True, exist_ok=True)
        self._perform(src, dst)
        return {"source": str(src), "destination": str(dst)}

    async def _run(
        self, source: str = "", destination: str = "", overwrite: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        if not source or not destination:
            raise ToolError("Both a source and a destination path are required.",
                            speech="I need both a source and a destination.")
        report = await self.to_thread(self._transfer, source, destination, bool(overwrite))
        logger.info("%s %s -> %s", self._verb, report["source"], report["destination"])
        return {
            **report,
            "speech": f"{self._past.capitalize()} {Path(report['source']).name} to "
                      f"{report['destination']}.",
            "artifacts": [report["destination"]],
        }


class CopyPathTool(_TransferTool):
    """Copy a file or directory tree to a new location inside the sandbox."""

    name = "copy_path"
    description = "Copy a file or folder to another location."
    permission_level = PermissionLevel.WRITE_ACTION
    category = ToolCategory.FILES
    aliases = ("copy_file", "duplicate_file")
    mutating = True
    _verb = "copy"
    _past = "copied"
    examples = (
        ToolExample(
            utterance="copy report.docx into backup",
            arguments={"source": "report.docx", "destination": "backup"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "source": {"type": "string", "description": "Existing file or folder to copy."},
            "destination": {"type": "string", "description": "Target path or existing folder."},
            "overwrite": {"type": "boolean", "description": "Replace the destination if it exists."},
        },
        required=["source", "destination"],
    )

    def _perform(self, source: Path, destination: Path) -> None:
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)


class MovePathTool(_TransferTool):
    """Move or rename a file or directory inside the sandbox."""

    name = "move_path"
    description = "Move or rename a file or folder."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.FILES
    aliases = ("rename_file", "move_file", "rename_path")
    mutating = True
    _verb = "move"
    _past = "moved"
    examples = (
        ToolExample(
            utterance="rename draft.md to final.md",
            arguments={"source": "draft.md", "destination": "final.md"},
        ),
        ToolExample(
            utterance="move the screenshots folder into archive",
            arguments={"source": "screenshots", "destination": "archive/screenshots"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "source": {"type": "string", "description": "Existing file or folder to move."},
            "destination": {"type": "string", "description": "New path or existing folder."},
            "overwrite": {"type": "boolean", "description": "Replace the destination if it exists."},
        },
        required=["source", "destination"],
    )

    def _perform(self, source: Path, destination: Path) -> None:
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        shutil.move(str(source), str(destination))


class DeletePathTool(BaseTool):
    """Delete a file or folder — to the recycle bin when send2trash exists.

    Always requires explicit confirmation (never auto-approved). As an extra
    belt-and-braces check, deleting an allowed sandbox root, the workspace, or
    the home directory wholesale is refused even after confirmation.
    """

    name = "delete_path"
    description = "Delete a file or folder (moved to the recycle bin when possible)."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.FILES
    aliases = ("delete_file", "remove_file")
    mutating = True
    examples = (
        ToolExample(utterance="delete old_logs.txt", arguments={"path": "old_logs.txt"}),
        ToolExample(utterance="remove the temp folder", arguments={"path": "temp"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={"path": {"type": "string", "description": "File or folder to delete."}},
        required=["path"],
    )

    def _delete(self, path: str) -> dict[str, Any]:
        target = resolve_sandboxed(path, must_exist=True)

        if _is_protected_root(target):
            raise ToolError(
                f"'{target}' is a workspace root; deleting it wholesale is refused. "
                "Delete specific files or subfolders instead.",
                speech="I won't delete an entire workspace folder.",
            )

        send2trash = try_import("send2trash")
        if send2trash is not None:
            try:
                send2trash.send2trash(str(target))
                return {"path": str(target), "method": "trash"}
            except Exception as exc:  # noqa: BLE001 - fall through to real delete
                logger.warning("send2trash failed for %s (%s); falling back.", target, exc)

        # Permanent removal: the CONFIRM_REQUIRED permission level (respecting
        # settings.REQUIRE_CONFIRM_FOR_DELETE) has already gated this call.
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"path": str(target), "method": "permanent"}

    async def _run(self, path: str = "", **kwargs: Any) -> dict[str, Any]:
        if not path:
            raise ToolError("A path is required.", speech="What should I delete?")
        report = await self.to_thread(self._delete, path)
        logger.info("Deleted %s via %s", report["path"], report["method"])
        how = "moved it to the recycle bin" if report["method"] == "trash" else "deleted it permanently"
        return {
            **report,
            "speech": f"Removed {Path(report['path']).name} — {how}.",
        }


class SearchFilesTool(BaseTool):
    """Find files by name pattern (glob or substring) and optionally by content.

    Content search reads only text files under 1 MB, case-insensitively, and
    reports the first matching line per file.
    """

    name = "search_files"
    description = "Search for files by name pattern and optionally by text content."
    permission_level = PermissionLevel.READ
    category = ToolCategory.FILES
    aliases = ("find_files", "find_file")
    examples = (
        ToolExample(utterance="find all markdown files", arguments={"pattern": "*.md"}),
        ToolExample(
            utterance="which files mention the word budget?",
            arguments={"pattern": "*", "content": "budget"},
        ),
        ToolExample(
            utterance="find report files in my documents",
            arguments={"pattern": "report", "path": "~/Documents"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "pattern": {"type": "string", "description": "Glob ('*.md') or name substring ('report')."},
            "path": {"type": "string", "description": "Folder to search (default: the workspace)."},
            "content": {"type": "string", "description": "Optional text to look for inside files (<1MB)."},
            "max_results": {"type": "integer", "description": "Result cap (default and max 100)."},
        },
        required=["pattern"],
    )

    @staticmethod
    def _name_matches(name: str, pattern: str) -> bool:
        if not pattern or pattern == "*":
            return True
        if any(ch in pattern for ch in "*?["):
            return fnmatch.fnmatch(name.lower(), pattern.lower())
        return pattern.lower() in name.lower()

    @staticmethod
    def _content_hit(path: Path, needle: str) -> tuple[int, str] | None:
        try:
            if path.stat().st_size > MAX_GREP_BYTES or looks_binary(path):
                return None
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        lowered = needle.lower()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if lowered in line.lower():
                return lineno, line.strip()[:200]
        return None

    def _search(
        self, pattern: str, path: str | None, content: str | None, max_results: int
    ) -> dict[str, Any]:
        root = resolve_sandboxed(path or paths.workspace_dir(), must_exist=True)
        if not root.is_dir():
            raise ToolError(f"'{root}' is not a directory.", speech="I can only search inside folders.")

        matches: list[dict[str, Any]] = []
        truncated = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for filename in sorted(filenames):
                if not self._name_matches(filename, pattern):
                    continue
                full = Path(dirpath) / filename
                record: dict[str, Any] = {"path": str(full), "name": filename}
                try:
                    record["size"] = full.stat().st_size
                except OSError:
                    record["size"] = None
                if content:
                    hit = self._content_hit(full, content)
                    if hit is None:
                        continue
                    record["line"], record["snippet"] = hit
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(record)
            if truncated:
                break
        return {"root": str(root), "matches": matches, "count": len(matches), "truncated": truncated}

    async def _run(
        self,
        pattern: str = "*",
        path: str | None = None,
        content: str | None = None,
        max_results: int = MAX_SEARCH_RESULTS,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not pattern and not content:
            raise ToolError("Give me a name pattern or text to search for.",
                            speech="What should I search for?")
        cap = max(1, min(int(max_results or MAX_SEARCH_RESULTS), MAX_SEARCH_RESULTS))
        report = await self.to_thread(self._search, pattern or "*", path, content, cap)
        what = f"'{pattern}'" + (f" containing '{content}'" if content else "")
        more = " (more exist)" if report["truncated"] else ""
        return {
            **report,
            "speech": f"Found {report['count']} files matching {what}{more}.",
            "display": "\n".join(m["path"] for m in report["matches"]) or "No matches.",
        }


class OpenPathTool(BaseTool):
    """Open a file or folder with the operating system's default application."""

    name = "open_path"
    description = "Open a file or folder with its default application."
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.FILES
    aliases = ("open_file", "open_folder", "show_in_folder")
    mutating = False
    examples = (
        ToolExample(utterance="open that PDF", arguments={"path": "outputs/report.pdf"}),
        ToolExample(utterance="show the projects folder", arguments={"path": "projects"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={"path": {"type": "string", "description": "File or folder to open."}},
        required=["path"],
    )

    def _open(self, path: str) -> dict[str, Any]:
        # Opening the home directory itself is a harmless read action the
        # sandbox would otherwise reject ("open my home folder").
        expanded = Path(os.path.expandvars(str(path))).expanduser()
        if expanded == paths.home_dir():
            return _open_with_os(str(expanded), sandboxed=False)
        return _open_with_os(path)

    async def _run(self, path: str = "", **kwargs: Any) -> dict[str, Any]:
        if not path:
            raise ToolError("A path is required.", speech="What should I open?")
        report = await self.to_thread(self._open, path)
        return {**report, "speech": f"Opened {Path(report['path']).name}."}


class FileInfoTool(BaseTool):
    """Report a path's size, timestamps, mime type and (for small text) lines."""

    name = "file_info"
    description = "Show size, timestamps, type and line count for a file or folder."
    permission_level = PermissionLevel.READ
    category = ToolCategory.FILES
    aliases = ("path_info", "stat_file")
    examples = (
        ToolExample(utterance="how big is video.mp4?", arguments={"path": "video.mp4"}),
        ToolExample(utterance="when was notes.txt last changed?", arguments={"path": "notes.txt"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={"path": {"type": "string", "description": "File or folder to inspect."}},
        required=["path"],
    )

    def _info(self, path: str) -> dict[str, Any]:
        target = resolve_sandboxed(path, must_exist=True)
        stat = target.stat()
        mime, _ = mimetypes.guess_type(target.name)
        info: dict[str, Any] = {
            "path": str(target),
            "name": target.name,
            "type": "directory" if target.is_dir() else "file",
            "size": stat.st_size,
            "size_human": human_size(stat.st_size),
            "modified": _iso(stat.st_mtime),
            "created": _iso(getattr(stat, "st_birthtime", stat.st_ctime)),
            "accessed": _iso(stat.st_atime),
            "mime_type": mime,
            "extension": target.suffix or None,
        }
        if target.is_dir():
            try:
                children = list(target.iterdir())
                info["item_count"] = len(children)
            except OSError:
                info["item_count"] = None
        elif stat.st_size <= MAX_GREP_BYTES and not looks_binary(target):
            try:
                info["line_count"] = target.read_text(
                    encoding="utf-8", errors="replace"
                ).count("\n") + 1
            except OSError:
                info["line_count"] = None
        return info

    async def _run(self, path: str = "", **kwargs: Any) -> dict[str, Any]:
        if not path:
            raise ToolError("A path is required.", speech="Which file should I look at?")
        info = await self.to_thread(self._info, path)
        if info["type"] == "directory":
            speech = f"{info['name']} is a folder with {info.get('item_count', '?')} items."
        else:
            speech = f"{info['name']} is {info['size_human']}, last modified {info['modified'][:10]}."
        return {**info, "speech": speech}



def _open_with_os(path: str, *, sandboxed: bool = True) -> dict[str, Any]:
    """Open a file or folder with the OS default application."""
    target = resolve_sandboxed(path, must_exist=True) if sandboxed else Path(path)
    osname = current_os()
    if is_windows():
        os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
    elif is_macos():
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            ["open", str(target)], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        if shutil.which("xdg-open") is None:
            raise ToolError(
                "No 'xdg-open' on this system — I can't open files with a default app here.",
                speech="This machine has no desktop opener installed.",
            )
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            ["xdg-open", str(target)], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return {"path": str(target), "os": osname}


# ---------------------------------------------------------------------------
# Find-and-open: "open my latest screenshot", "open that ppt I made"
# ---------------------------------------------------------------------------

#: What a spoken "kind" means: which folders to look in, which extensions count.
FILE_KINDS: dict[str, dict[str, Any]] = {
    "screenshot": {"dirs": [lambda: paths.screenshots_dir(), lambda: paths.home_dir() / "Pictures"],
                    "exts": (".png", ".jpg", ".jpeg")},
    "photo":      {"dirs": [lambda: paths.home_dir() / "Pictures", lambda: paths.screenshots_dir(),
                             lambda: paths.home_dir() / "Downloads"],
                    "exts": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp")},
    "presentation": {"dirs": [lambda: paths.outputs_dir(), lambda: paths.home_dir() / "Documents",
                               lambda: paths.home_dir() / "Downloads"],
                    "exts": (".pptx", ".ppt", ".html")},
    "document":   {"dirs": [lambda: paths.outputs_dir(), lambda: paths.home_dir() / "Documents",
                             lambda: paths.home_dir() / "Downloads"],
                    "exts": (".docx", ".doc", ".pdf", ".md", ".txt", ".rtf", ".odt")},
    "pdf":        {"dirs": [lambda: paths.home_dir() / "Downloads", lambda: paths.outputs_dir(),
                             lambda: paths.home_dir() / "Documents"],
                    "exts": (".pdf",)},
    "spreadsheet": {"dirs": [lambda: paths.outputs_dir(), lambda: paths.home_dir() / "Documents",
                              lambda: paths.home_dir() / "Downloads"],
                    "exts": (".xlsx", ".xls", ".csv", ".ods")},
    "video":      {"dirs": [lambda: paths.home_dir() / "Videos", lambda: paths.home_dir() / "Downloads"],
                    "exts": (".mp4", ".mkv", ".mov", ".avi", ".webm")},
    "song":       {"dirs": [lambda: paths.home_dir() / "Music", lambda: paths.home_dir() / "Downloads"],
                    "exts": (".mp3", ".m4a", ".wav", ".flac", ".ogg")},
    "download":   {"dirs": [lambda: paths.home_dir() / "Downloads"], "exts": ()},
    "code":       {"dirs": [lambda: paths.projects_dir(), lambda: paths.outputs_dir()],
                    "exts": (".py", ".js", ".ts", ".html", ".ino", ".cpp", ".java")},
    "file":       {"dirs": [lambda: paths.workspace_dir(), lambda: paths.home_dir() / "Downloads",
                             lambda: paths.home_dir() / "Documents", lambda: paths.home_dir() / "Desktop"],
                    "exts": ()},
}

#: Spoken aliases -> canonical kind key.
KIND_ALIASES = {
    "screenshot": "screenshot", "screen shot": "screenshot",
    "photo": "photo", "picture": "photo", "image": "photo", "pic": "photo",
    "presentation": "presentation", "ppt": "presentation", "deck": "presentation",
    "slides": "presentation", "slideshow": "presentation",
    "document": "document", "doc": "document", "word doc": "document",
    "pdf": "pdf",
    "spreadsheet": "spreadsheet", "excel": "spreadsheet", "sheet": "spreadsheet", "csv": "spreadsheet",
    "video": "video", "movie": "video",
    "song": "song", "music file": "song", "audio": "song",
    "download": "download", "downloaded file": "download",
    "code": "code", "script": "code",
    "file": "file",
}


def _iter_candidate_files(kind_spec: dict[str, Any], max_entries: int = 4000):
    """Yield existing files for a kind spec, bounded for responsiveness."""
    seen: set[Path] = set()
    count = 0
    for dir_factory in kind_spec["dirs"]:
        try:
            root = Path(dir_factory()).expanduser()
        except OSError:
            continue
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        exts = kind_spec["exts"]
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")][:40]
            depth = len(Path(current).relative_to(root).parts)
            if depth >= 3:
                dirnames[:] = []
            for filename in filenames:
                if filename.startswith("."):
                    continue
                if exts and not filename.lower().endswith(exts):
                    continue
                yield Path(current) / filename
                count += 1
                if count >= max_entries:
                    return


class FindAndOpenTool(BaseTool):
    """Find a file by kind or fuzzy name across the user's folders and open it.

    Powers "open my latest screenshot", "open that ppt I made" and
    "open the budget spreadsheet" without the user knowing any paths.
    """

    name = "find_and_open"
    description = (
        "Find a file by kind (screenshot, photo, ppt, document, pdf, video, download...) "
        "or by fuzzy name across the user's folders, then open it. Use latest=true for "
        "'the newest one'."
    )
    permission_level = PermissionLevel.DESKTOP_ACTION
    category = ToolCategory.FILES
    aliases = ("open_latest", "open_recent", "find_file_and_open")
    examples = (
        ToolExample(utterance="open my latest screenshot", arguments={"kind": "screenshot", "latest": True}),
        ToolExample(utterance="open that ppt I made", arguments={"kind": "presentation", "latest": True}),
        ToolExample(utterance="open the budget spreadsheet", arguments={"kind": "spreadsheet", "name": "budget"}),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "kind": {"type": "string", "description": "File kind: screenshot, photo, presentation, document, pdf, spreadsheet, video, song, download, code, file."},
            "name": {"type": "string", "description": "Fuzzy file name to look for (optional)."},
            "latest": {"type": "boolean", "description": "Pick the most recently modified match."},
        },
    )

    def _find(self, kind: str, name: str, latest: bool) -> Path:
        import difflib

        kind_key = KIND_ALIASES.get((kind or "file").strip().lower(), "file")
        spec = FILE_KINDS[kind_key]
        needle = (name or "").strip().lower()

        # Ranking: with a name, best fuzzy match wins (recency breaks ties);
        # with latest (or no name at all), newest file wins.
        best_key: tuple[float, float] | None = None
        best_path: Path | None = None
        for candidate in _iter_candidate_files(spec):
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if needle:
                stem = candidate.stem.lower().replace("-", " ").replace("_", " ")
                similarity = difflib.SequenceMatcher(None, needle, stem).ratio()
                if needle in stem:
                    similarity = max(similarity, 0.85)
                if similarity < 0.45:
                    continue
                key = (mtime, similarity) if latest else (similarity, mtime)
            else:
                key = (mtime, 0.0)
            if best_key is None or key > best_key:
                best_key, best_path = key, candidate

        if best_path is None:
            what = f"a {kind_key}" + (f" named '{name}'" if name else "")
            raise ToolError(
                f"I couldn't find {what} in your folders.",
                speech=f"I couldn't find {what}.",
            )
        return best_path

    async def _run(
        self, kind: str = "file", name: str = "", latest: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        target = await self.to_thread(self._find, kind, name, bool(latest))
        report = await self.to_thread(_open_with_os, str(target))
        modified = datetime.fromtimestamp(target.stat().st_mtime).strftime("%b %d, %H:%M")
        return {
            **report,
            "modified": modified,
            "speech": f"Opened {target.name}.",
            "display": f"Opened {target} (modified {modified})",
        }


def get_tools() -> list[BaseTool]:
    return [
        ListDirectoryTool(),
        ReadFileTool(),
        WriteFileTool(),
        CopyPathTool(),
        MovePathTool(),
        DeletePathTool(),
        SearchFilesTool(),
        OpenPathTool(),
        FindAndOpenTool(),
        FileInfoTool(),
    ]
