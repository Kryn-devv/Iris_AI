"""Tests for the code-writer (content) and file-manager (files) tool modules.

All filesystem activity is redirected into pytest's ``tmp_path``: the IRIS
workspace is monkeypatched and a fresh :class:`PathSandbox` rooted at
``tmp_path`` replaces the default sandbox in every module that imported it.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from iris.app.core import paths, security
from iris.app.core.config import settings
from iris.app.core.security import PathSandbox
from iris.app.tools.content import code_writer
from iris.app.tools.files import file_manager
from iris.app.tools.content.code_writer import (
    RunPythonTool,
    ScaffoldProjectTool,
    WriteCodeTool,
    infer_language,
    project_template,
)
from iris.app.tools.files.file_manager import (
    CopyPathTool,
    DeletePathTool,
    FileInfoTool,
    ListDirectoryTool,
    MovePathTool,
    OpenPathTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
    human_size,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Redirect the IRIS workspace and the path sandbox into tmp_path."""
    root = tmp_path.resolve()
    workspace = root / "workspace"
    (workspace / "projects").mkdir(parents=True)

    monkeypatch.setattr(paths, "workspace_dir", lambda: workspace)

    box = PathSandbox(allowed_roots=[root], denied_patterns=list(settings.FS_DENIED_PATTERNS))
    monkeypatch.setattr(security, "default_path_sandbox", box)
    monkeypatch.setattr(code_writer, "default_path_sandbox", box)
    monkeypatch.setattr(file_manager, "default_path_sandbox", box)
    return workspace


# =============================================================================
# code_writer: write_code
# =============================================================================


async def test_write_code_creates_file_with_artifact(ws):
    tool = WriteCodeTool()
    res = await tool.execute(filename="hello.py", code="print('hi')\n")
    assert res.success is True
    target = ws / "projects" / "hello.py"
    assert target.exists()
    assert target.read_text() == "print('hi')\n"
    assert res.result["language"] == "python"
    assert res.artifacts == [str(target)]
    assert res.speech


async def test_write_code_project_subfolder_and_nested_filename(ws):
    tool = WriteCodeTool()
    res = await tool.execute(filename="src/utils/dates.py", code="x = 1\n", project="tracker")
    assert res.success is True
    assert (ws / "projects" / "tracker" / "src" / "utils" / "dates.py").exists()
    assert res.result["project"] == "tracker"


async def test_write_code_refuses_traversal(ws):
    tool = WriteCodeTool()
    res = await tool.execute(filename="../evil.py", code="boom")
    assert res.success is False
    assert "refused" in res.error or "parent" in res.error.lower()
    assert not (ws / "evil.py").exists()

    res2 = await tool.execute(filename="ok.py", code="boom", project="../outside")
    assert res2.success is False


async def test_write_code_refuses_absolute_path(ws, tmp_path):
    tool = WriteCodeTool()
    res = await tool.execute(filename=str(tmp_path / "abs.py"), code="x")
    assert res.success is False


async def test_write_code_size_cap(ws, monkeypatch):
    monkeypatch.setattr(code_writer.settings, "FS_MAX_WRITE_BYTES", 8)
    tool = WriteCodeTool()
    res = await tool.execute(filename="big.py", code="x" * 100)
    assert res.success is False
    assert "byte" in res.error.lower()


def test_infer_language_table():
    assert infer_language("app.py") == "python"
    assert infer_language("index.TS".lower()) == "typescript"
    assert infer_language("style.css") == "css"
    assert infer_language("Dockerfile") == "dockerfile"
    assert infer_language("weird.xyz") == "unknown"
    assert infer_language("main.py", explicit="Cython") == "cython"


# =============================================================================
# code_writer: scaffold_project
# =============================================================================


async def test_scaffold_python_template(ws):
    tool = ScaffoldProjectTool()
    res = await tool.execute(name="demo", template="python")
    assert res.success is True
    root = ws / "projects" / "demo"
    for expected in ("main.py", "README.md", ".gitignore", "requirements.txt"):
        assert (root / expected).exists(), expected
    assert set(res.result["files"]) >= {"main.py", "README.md", ".gitignore", "requirements.txt"}


async def test_scaffold_refuses_existing_non_empty(ws):
    tool = ScaffoldProjectTool()
    assert (await tool.execute(name="demo")).success is True
    res = await tool.execute(name="demo")
    assert res.success is False
    assert "exists" in res.error


async def test_scaffold_template_file_sets(ws):
    tool = ScaffoldProjectTool()
    expectations = {
        "python-api": {"app.py", "requirements.txt"},
        "web": {"index.html", "style.css", "app.js"},
        "node": {"package.json", "index.js"},
    }
    for template, files in expectations.items():
        res = await tool.execute(name=f"proj-{template}", template=template)
        assert res.success is True, res.error
        assert set(res.result["files"]) >= files
    api_app = (ws / "projects" / "proj-python-api" / "app.py").read_text()
    assert "/health" in api_app and "FastAPI" in api_app
    html = (ws / "projects" / "proj-web" / "index.html").read_text()
    assert "style.css" in html and "app.js" in html


async def test_scaffold_unknown_template_refused(ws):
    res = await ScaffoldProjectTool().execute(name="whatever", template="cobol")
    assert res.success is False
    assert "template" in res.error.lower()


def test_project_template_pure_helper():
    files = project_template("node", "My App")
    assert '"name": "my-app"' in files["package.json"]


# =============================================================================
# code_writer: run_python
# =============================================================================


async def test_run_python_snippet_captures_output(ws):
    tool = RunPythonTool()
    res = await tool.execute(code="print(2 + 3)")
    assert res.success is True
    assert res.result["exit_code"] == 0
    assert "5" in res.result["stdout"]
    assert res.result["timed_out"] is False
    # The temp snippet is cleaned up afterwards.
    snippets = ws / "projects" / ".snippets"
    assert not any(snippets.glob("*.py"))


async def test_run_python_snippet_stderr_and_exit_code(ws):
    res = await RunPythonTool().execute(code="import sys; sys.stderr.write('bad'); sys.exit(3)")
    assert res.success is True
    assert res.result["exit_code"] == 3
    assert "bad" in res.result["stderr"]


async def test_run_python_timeout(ws):
    tool = RunPythonTool()
    res = await tool.execute(code="import time\ntime.sleep(10)\nprint('never')", timeout_seconds=1)
    assert res.success is True
    assert res.result["timed_out"] is True
    assert "never" not in res.result["stdout"]


async def test_run_python_file_in_project(ws):
    scaffold = await ScaffoldProjectTool().execute(name="runme", template="python")
    assert scaffold.success is True
    res = await RunPythonTool().execute(file="runme/main.py")
    assert res.success is True, res.error
    assert res.result["exit_code"] == 0
    assert "Hello from runme" in res.result["stdout"]


async def test_run_python_requires_exactly_one_of_file_or_code(ws):
    tool = RunPythonTool()
    assert (await tool.execute()).success is False
    assert (await tool.execute(file="a.py", code="print(1)")).success is False


async def test_run_python_refuses_files_outside_projects_dir(ws):
    outside = ws / "loose.py"
    outside.write_text("print('nope')\n")
    res = await RunPythonTool().execute(file=str(outside))
    assert res.success is False
    assert "projects" in res.error.lower()


# =============================================================================
# file_manager: list / read / write
# =============================================================================


async def test_list_directory_dirs_first_and_hidden_filter(ws):
    (ws / "zebra.txt").write_text("z")
    (ws / "alpha").mkdir()
    (ws / ".hidden").write_text("h")

    res = await ListDirectoryTool().execute(path=str(ws))
    assert res.success is True
    names = [e["name"] for e in res.result["entries"]]
    assert ".hidden" not in names
    assert names.index("alpha") < names.index("zebra.txt")  # dirs first
    assert res.result["truncated"] is False

    res_hidden = await ListDirectoryTool().execute(path=str(ws), show_hidden=True)
    assert ".hidden" in [e["name"] for e in res_hidden.result["entries"]]


async def test_list_directory_refuses_escape(ws):
    res = await ListDirectoryTool().execute(path="/etc")
    assert res.success is False
    assert "outside the allowed workspace" in res.error


async def test_read_file_truncation_flag(ws):
    target = ws / "notes.txt"
    target.write_text("hello world, this is a longer file")
    res = await ReadFileTool().execute(path=str(target), max_bytes=5)
    assert res.success is True
    assert res.result["text"] == "hello"
    assert res.result["truncated"] is True

    full = await ReadFileTool().execute(path=str(target))
    assert full.result["truncated"] is False


async def test_read_file_refuses_binary(ws):
    target = ws / "blob.bin"
    target.write_bytes(b"\x00\x01\x02PNG")
    res = await ReadFileTool().execute(path=str(target))
    assert res.success is False
    assert "binary" in res.error.lower()


async def test_read_file_traversal_escape_refused(ws):
    res = await ReadFileTool().execute(path="../../outside.txt")
    assert res.success is False
    assert "outside the allowed workspace" in res.error


async def test_read_file_denied_pattern(ws):
    secret = ws / ".env"
    secret.write_text("API_KEY=123")
    res = await ReadFileTool().execute(path=str(secret))
    assert res.success is False
    assert "protected pattern" in res.error


async def test_write_file_creates_parents_and_appends(ws):
    tool = WriteFileTool()
    res = await tool.execute(path="deep/nested/x.txt", content="one\n")
    assert res.success is True
    target = ws / "deep" / "nested" / "x.txt"
    assert target.read_text() == "one\n"

    res2 = await tool.execute(path="deep/nested/x.txt", content="two\n", append=True)
    assert res2.success is True
    assert target.read_text() == "one\ntwo\n"

    res3 = await tool.execute(path="deep/nested/x.txt", content="fresh\n")
    assert target.read_text() == "fresh\n"


async def test_write_file_size_cap(ws, monkeypatch):
    monkeypatch.setattr(file_manager.settings, "FS_MAX_WRITE_BYTES", 4)
    res = await WriteFileTool().execute(path="cap.txt", content="too long")
    assert res.success is False


# =============================================================================
# file_manager: copy / move / delete
# =============================================================================


async def test_copy_refuses_overwrite_unless_flag(ws):
    (ws / "a.txt").write_text("A")
    (ws / "b.txt").write_text("B")
    tool = CopyPathTool()

    res = await tool.execute(source="a.txt", destination="b.txt")
    assert res.success is False
    assert "overwrite" in res.error.lower()
    assert (ws / "b.txt").read_text() == "B"

    res2 = await tool.execute(source="a.txt", destination="b.txt", overwrite=True)
    assert res2.success is True
    assert (ws / "b.txt").read_text() == "A"


async def test_copy_directory_and_into_existing_folder(ws):
    src = ws / "folder"
    src.mkdir()
    (src / "inner.txt").write_text("data")
    dest_parent = ws / "backup"
    dest_parent.mkdir()

    res = await CopyPathTool().execute(source="folder", destination="backup")
    assert res.success is True
    assert (dest_parent / "folder" / "inner.txt").read_text() == "data"
    assert (src / "inner.txt").exists()  # copy keeps the source


async def test_move_renames_and_guards_overwrite(ws):
    (ws / "draft.md").write_text("draft")
    tool = MovePathTool()
    res = await tool.execute(source="draft.md", destination="final.md")
    assert res.success is True
    assert not (ws / "draft.md").exists()
    assert (ws / "final.md").read_text() == "draft"

    (ws / "other.md").write_text("other")
    res2 = await tool.execute(source="other.md", destination="final.md")
    assert res2.success is False
    assert (ws / "final.md").read_text() == "draft"

    res3 = await tool.execute(source="other.md", destination="final.md", overwrite=True)
    assert res3.success is True
    assert (ws / "final.md").read_text() == "other"


async def test_delete_file_and_folder(ws):
    target = ws / "junk.txt"
    target.write_text("junk")
    res = await DeletePathTool().execute(path="junk.txt")
    assert res.success is True
    assert not target.exists()
    assert res.result["method"] in ("trash", "permanent")

    folder = ws / "tempdir"
    (folder / "sub").mkdir(parents=True)
    res2 = await DeletePathTool().execute(path="tempdir")
    assert res2.success is True
    assert not folder.exists()


async def test_delete_refuses_workspace_and_sandbox_root(ws, tmp_path):
    res = await DeletePathTool().execute(path=str(ws))
    assert res.success is False
    assert "root" in res.error.lower()
    assert ws.exists()

    res2 = await DeletePathTool().execute(path=str(tmp_path))
    assert res2.success is False
    assert tmp_path.exists()


# =============================================================================
# file_manager: search / open / info
# =============================================================================


async def test_search_by_glob_substring_and_content(ws):
    (ws / "notes.md").write_text("line one\nthe BUDGET is tight\n")
    (ws / "report.txt").write_text("nothing here")
    sub = ws / "sub"
    sub.mkdir()
    (sub / "plan.md").write_text("more budget talk")
    (ws / "image.bin").write_bytes(b"\x00budget")  # binary: skipped by content grep

    glob_res = await SearchFilesTool().execute(pattern="*.md", path=str(ws))
    assert glob_res.success is True
    names = {m["name"] for m in glob_res.result["matches"]}
    assert names == {"notes.md", "plan.md"}

    sub_res = await SearchFilesTool().execute(pattern="report", path=str(ws))
    assert {m["name"] for m in sub_res.result["matches"]} == {"report.txt"}

    content_res = await SearchFilesTool().execute(pattern="*", path=str(ws), content="budget")
    assert content_res.success is True
    matched = {m["name"] for m in content_res.result["matches"]}
    assert matched == {"notes.md", "plan.md"}
    by_name = {m["name"]: m for m in content_res.result["matches"]}
    assert by_name["notes.md"]["line"] == 2


async def test_search_refuses_outside_sandbox(ws):
    res = await SearchFilesTool().execute(pattern="*", path="/usr")
    assert res.success is False


async def test_open_path_linux_uses_xdg_open_detached(ws, monkeypatch):
    target = ws / "doc.txt"
    target.write_text("hi")

    calls: list = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))

        class _P:
            pid = 4242

        return _P()

    monkeypatch.setattr(file_manager, "is_windows", lambda: False)
    monkeypatch.setattr(file_manager, "is_macos", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    res = await OpenPathTool().execute(path=str(target))
    assert res.success is True
    argv, kwargs = calls[0]
    assert argv == ["xdg-open", str(target)]
    assert kwargs.get("start_new_session") is True


async def test_open_path_missing_opener_fails_cleanly(ws, monkeypatch):
    target = ws / "doc.txt"
    target.write_text("hi")
    monkeypatch.setattr(file_manager, "is_windows", lambda: False)
    monkeypatch.setattr(file_manager, "is_macos", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = await OpenPathTool().execute(path=str(target))
    assert res.success is False
    assert "xdg-open" in res.error


async def test_file_info_text_file(ws):
    target = ws / "info.txt"
    target.write_text("one\ntwo\nthree\n")
    res = await FileInfoTool().execute(path="info.txt")
    assert res.success is True
    info = res.result
    assert info["type"] == "file"
    assert info["size"] == len("one\ntwo\nthree\n")
    assert info["mime_type"] == "text/plain"
    assert info["line_count"] == 4  # trailing newline counts one extra split
    assert info["modified"]


async def test_file_info_directory(ws):
    (ws / "d").mkdir()
    (ws / "d" / "x.txt").write_text("x")
    res = await FileInfoTool().execute(path="d")
    assert res.success is True
    assert res.result["type"] == "directory"
    assert res.result["item_count"] == 1


def test_human_size_helper():
    assert human_size(10) == "10 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


def test_get_tools_exports():
    code_names = {t.name for t in code_writer.get_tools()}
    assert code_names == {"write_code", "scaffold_project", "run_python"}
    file_names = {t.name for t in file_manager.get_tools()}
    assert file_names == {
        "list_directory", "read_file", "write_file", "copy_path", "move_path",
        "delete_path", "search_files", "open_path", "find_and_open", "file_info",
    }
    for tool in list(code_writer.get_tools()) + list(file_manager.get_tools()):
        assert tool.description
        assert tool.examples, tool.name
