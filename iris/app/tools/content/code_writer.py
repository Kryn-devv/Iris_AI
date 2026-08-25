"""Code writing, project scaffolding and sandboxed Python execution tools.

This module gives IRIS a spoken-language friendly coding surface:

* :class:`WriteCodeTool`      — "write a python script that prints fibonacci"
* :class:`ScaffoldProjectTool` — "start a new fastapi project called tracker"
* :class:`RunPythonTool`      — "run main.py in my tracker project"

Everything lands inside ``paths.projects_dir()`` and every path is validated
through the filesystem sandbox (:data:`default_path_sandbox`), so generated
code can never overwrite files outside the IRIS workspace. Execution uses the
current interpreter (``sys.executable``) in a fresh subprocess with **no
shell**, a hard wall-clock timeout and capped output capture.

Pure helpers exported for the NLU layer and tests:

* :func:`infer_language` — file extension → human language name.
* :func:`project_template` — template id → ``{relative path: content}`` map.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.core.security import PermissionLevel, SandboxError, default_path_sandbox
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.content.code_writer")

__all__ = [
    "infer_language",
    "project_template",
    "TEMPLATE_NAMES",
    "WriteCodeTool",
    "ScaffoldProjectTool",
    "RunPythonTool",
    "get_tools",
]

#: Hard wall-clock limit for user code executed by :class:`RunPythonTool`.
RUN_TIMEOUT_SECONDS = 15.0
#: Maximum characters of stdout/stderr each that are returned to the model.
OUTPUT_CAP_CHARS = 10_000
#: Maximum size of an inline snippet accepted by :class:`RunPythonTool`.
MAX_SNIPPET_CHARS = 50_000

#: File extension → language name, used to describe what was written.
_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python", ".pyw": "python", ".ipynb": "jupyter notebook",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript (react)", ".jsx": "javascript (react)",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".sass": "sass",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini",
    ".md": "markdown", ".rst": "restructuredtext", ".txt": "plain text",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "powershell",
    ".bat": "batch", ".cmd": "batch",
    ".c": "c", ".h": "c header", ".cpp": "c++", ".cc": "c++", ".hpp": "c++ header",
    ".cs": "c#", ".java": "java", ".kt": "kotlin", ".swift": "swift",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".lua": "lua",
    ".r": "r", ".jl": "julia", ".sql": "sql", ".xml": "xml", ".svg": "svg",
    ".vue": "vue", ".svelte": "svelte", ".dart": "dart", ".scala": "scala",
    ".dockerfile": "dockerfile", ".env.example": "dotenv example",
}


def infer_language(filename: str, explicit: str | None = None) -> str:
    """Best-effort language name for ``filename``.

    An explicitly supplied language always wins; otherwise the extension is
    looked up in :data:`_EXTENSION_LANGUAGES`, with a couple of special-cased
    extensionless names (``Dockerfile``, ``Makefile``).
    """
    if explicit and explicit.strip():
        return explicit.strip().lower()
    name = Path(filename).name.lower()
    if name in ("dockerfile",):
        return "dockerfile"
    if name in ("makefile", "gnumakefile"):
        return "makefile"
    return _EXTENSION_LANGUAGES.get(Path(name).suffix, "unknown")


def _reject_traversal(value: str, what: str) -> None:
    """Refuse any component-wise ``..`` in a user-supplied relative path."""
    parts = Path(value.replace("\\", "/")).parts
    if ".." in parts:
        raise ToolError(
            f"The {what} '{value}' contains a parent-directory reference and was refused.",
            speech="I can't write outside the projects folder.",
        )
    if value.startswith(("/", "\\")) or (len(value) > 1 and value[1] == ":"):
        raise ToolError(
            f"The {what} must be relative to the projects folder, not absolute: '{value}'.",
            speech="Please give me a name relative to the projects folder.",
        )


# =============================================================================
# Project templates
# =============================================================================


def _python_template(name: str) -> dict[str, str]:
    return {
        "main.py": (
            f'"""Entry point for {name}."""\n\n\n'
            "def main() -> None:\n"
            f'    print("Hello from {name}!")\n\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "README.md": (
            f"# {name}\n\nA Python project scaffolded by IRIS.\n\n"
            "## Run\n\n```bash\npython main.py\n```\n"
        ),
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\nvenv/\n.env\ndist/\nbuild/\n*.egg-info/\n",
        "requirements.txt": "# Add project dependencies here\n",
    }


def _python_api_template(name: str) -> dict[str, str]:
    return {
        "app.py": (
            f'"""FastAPI application for {name}."""\n\n'
            "from fastapi import FastAPI\n\n"
            f'app = FastAPI(title="{name}")\n\n\n'
            '@app.get("/health")\n'
            "async def health() -> dict:\n"
            '    """Liveness probe."""\n'
            '    return {"status": "ok", "service": "' + name + '"}\n\n\n'
            '@app.get("/")\n'
            "async def root() -> dict:\n"
            f'    return {{"message": "Welcome to {name}"}}\n'
        ),
        "requirements.txt": "fastapi>=0.110.0\nuvicorn[standard]>=0.28.0\n",
        "README.md": (
            f"# {name}\n\nA FastAPI service scaffolded by IRIS.\n\n"
            "## Run\n\n```bash\npip install -r requirements.txt\nuvicorn app:app --reload\n```\n\n"
            "Check http://127.0.0.1:8000/health\n"
        ),
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\nvenv/\n.env\n",
    }


def _web_template(name: str) -> dict[str, str]:
    return {
        "index.html": (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n<head>\n'
            '  <meta charset="UTF-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            f"  <title>{name}</title>\n"
            '  <link rel="stylesheet" href="style.css">\n'
            "</head>\n<body>\n"
            '  <main class="container">\n'
            f"    <h1>{name}</h1>\n"
            '    <p class="subtitle">Scaffolded by IRIS. Edit <code>index.html</code>,\n'
            "      <code>style.css</code> and <code>app.js</code> to get started.</p>\n"
            '    <button id="action">Click me</button>\n'
            '    <p id="output"></p>\n'
            "  </main>\n"
            '  <script src="app.js"></script>\n'
            "</body>\n</html>\n"
        ),
        "style.css": (
            ":root {\n"
            "  --bg: #0f1115;\n  --panel: #171a21;\n  --text: #e6e9ef;\n"
            "  --muted: #9aa4b2;\n  --accent: #4f8cff;\n"
            "}\n\n"
            "* { box-sizing: border-box; margin: 0; padding: 0; }\n\n"
            "body {\n"
            "  background: var(--bg);\n  color: var(--text);\n"
            "  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;\n"
            "  min-height: 100vh;\n  display: grid;\n  place-items: center;\n"
            "}\n\n"
            ".container {\n"
            "  background: var(--panel);\n  padding: 3rem;\n  border-radius: 12px;\n"
            "  max-width: 640px;\n  text-align: center;\n"
            "}\n\n"
            "h1 { margin-bottom: 0.75rem; }\n"
            ".subtitle { color: var(--muted); margin-bottom: 1.5rem; }\n\n"
            "button {\n"
            "  background: var(--accent);\n  color: white;\n  border: none;\n"
            "  padding: 0.6rem 1.4rem;\n  border-radius: 8px;\n  font-size: 1rem;\n"
            "  cursor: pointer;\n"
            "}\n"
            "button:hover { filter: brightness(1.1); }\n"
            "#output { margin-top: 1rem; color: var(--muted); }\n"
        ),
        "app.js": (
            '"use strict";\n\n'
            "let clicks = 0;\n"
            'document.getElementById("action").addEventListener("click", () => {\n'
            "  clicks += 1;\n"
            '  document.getElementById("output").textContent = `Clicked ${clicks} time(s)`;\n'
            "});\n"
        ),
    }


def _node_template(name: str) -> dict[str, str]:
    return {
        "package.json": (
            "{\n"
            f'  "name": "{name.lower().replace(" ", "-")}",\n'
            '  "version": "0.1.0",\n'
            '  "description": "Scaffolded by IRIS",\n'
            '  "main": "index.js",\n'
            '  "type": "commonjs",\n'
            '  "scripts": {\n    "start": "node index.js"\n  }\n'
            "}\n"
        ),
        "index.js": (
            '"use strict";\n\n'
            f'console.log("Hello from {name}!");\n'
        ),
    }


_TEMPLATES = {
    "python": _python_template,
    "python-api": _python_api_template,
    "web": _web_template,
    "node": _node_template,
}

#: Public tuple of valid template identifiers.
TEMPLATE_NAMES: tuple[str, ...] = tuple(_TEMPLATES)


def project_template(template: str, name: str) -> dict[str, str]:
    """Return the ``{relative path: content}`` file map for a template id."""
    builder = _TEMPLATES.get((template or "python").strip().lower())
    if builder is None:
        raise ToolError(
            f"Unknown project template '{template}'. Choose one of: {', '.join(TEMPLATE_NAMES)}.",
            speech="I don't know that project template.",
        )
    return builder(name)


# =============================================================================
# Tools
# =============================================================================


class WriteCodeTool(BaseTool):
    """Write a source file into the IRIS projects folder.

    The filename may contain subdirectories (``src/utils/helpers.py``), and an
    optional ``project`` argument selects a project subfolder. Absolute paths
    and ``..`` traversal are refused, the resulting path is validated by the
    filesystem sandbox, and the file size is capped by
    ``settings.FS_MAX_WRITE_BYTES``.
    """

    name = "write_code"
    description = "Write a source-code file into the IRIS projects folder."
    permission_level = PermissionLevel.WRITE_ACTION
    category = ToolCategory.CODE
    aliases = ("save_code", "create_script", "write_program")
    mutating = True
    examples = (
        ToolExample(
            utterance="write a python script fib.py that prints fibonacci numbers",
            arguments={"filename": "fib.py", "code": "print('fib')"},
        ),
        ToolExample(
            utterance="save this helper into my tracker project as utils/dates.py",
            arguments={"filename": "utils/dates.py", "code": "...", "project": "tracker"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "filename": {
                "type": "string",
                "description": "File name, optionally with subdirectories, e.g. 'src/main.py'.",
            },
            "code": {"type": "string", "description": "Full source code to write."},
            "project": {
                "type": "string",
                "description": "Optional project subfolder inside the projects directory.",
            },
            "language": {
                "type": "string",
                "description": "Optional language label; inferred from the extension when omitted.",
            },
        },
        required=["filename", "code"],
    )

    def _write(self, filename: str, code: str, project: str | None) -> Path:
        base = paths.projects_dir()
        if project:
            _reject_traversal(project, "project name")
            base = base / project
        _reject_traversal(filename, "filename")

        try:
            target = default_path_sandbox.resolve(base / filename)
        except SandboxError as exc:
            raise ToolError(str(exc), speech="That path is outside my allowed workspace.") from exc

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        return target

    async def _run(
        self,
        filename: str = "",
        code: str = "",
        project: str | None = None,
        language: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not filename or not filename.strip():
            raise ToolError("A filename is required.", speech="What should I name the file?")
        if code is None or code == "":
            raise ToolError("No code was provided to write.", speech="There was no code to save.")

        encoded = code.encode("utf-8")
        if len(encoded) > settings.FS_MAX_WRITE_BYTES:
            raise ToolError(
                f"The code is {len(encoded)} bytes, above the {settings.FS_MAX_WRITE_BYTES} byte limit.",
                speech="That file is too large for me to write.",
            )

        target = await self.to_thread(self._write, filename.strip(), code, project)
        lang = infer_language(filename, language)
        line_count = code.count("\n") + (0 if code.endswith("\n") else 1)
        logger.info("Wrote %s (%d bytes, %s)", target, len(encoded), lang)
        return {
            "path": str(target),
            "filename": filename.strip(),
            "project": project or None,
            "language": lang,
            "bytes_written": len(encoded),
            "lines": line_count,
            "speech": f"Saved {Path(filename).name} — {line_count} lines of {lang}.",
            "display": f"Wrote {len(encoded)} bytes of {lang} to {target}",
            "artifacts": [str(target)],
        }


class ScaffoldProjectTool(BaseTool):
    """Create a runnable starter project inside the IRIS projects folder.

    Supported templates: ``python`` (script + README + .gitignore +
    requirements), ``python-api`` (FastAPI app with a ``/health`` route),
    ``web`` (dark-themed HTML/CSS/JS starter page) and ``node``
    (package.json + index.js). Refuses to scaffold into an existing non-empty
    directory so nothing is ever overwritten.
    """

    name = "scaffold_project"
    description = "Create a new starter code project (python, python-api, web or node)."
    permission_level = PermissionLevel.WRITE_ACTION
    category = ToolCategory.CODE
    aliases = ("create_project", "new_project", "start_project")
    mutating = True
    examples = (
        ToolExample(
            utterance="start a new python project called budget-tracker",
            arguments={"name": "budget-tracker", "template": "python"},
        ),
        ToolExample(
            utterance="scaffold a fastapi service named notes-api",
            arguments={"name": "notes-api", "template": "python-api"},
        ),
        ToolExample(
            utterance="make me a simple website project called portfolio",
            arguments={"name": "portfolio", "template": "web"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "name": {"type": "string", "description": "Project folder name, e.g. 'budget-tracker'."},
            "template": {
                "type": "string",
                "enum": list(TEMPLATE_NAMES),
                "description": "Project template to scaffold (default 'python').",
            },
        },
        required=["name"],
    )

    def _scaffold(self, name: str, template: str) -> tuple[Path, list[str]]:
        _reject_traversal(name, "project name")
        if "/" in name or "\\" in name:
            raise ToolError(
                "A project name must be a single folder name without path separators.",
                speech="Project names can't contain slashes.",
            )

        files = project_template(template, name)
        try:
            root = default_path_sandbox.resolve(paths.projects_dir() / name)
        except SandboxError as exc:
            raise ToolError(str(exc), speech="That location is outside my allowed workspace.") from exc

        if root.exists() and any(root.iterdir()):
            raise ToolError(
                f"A non-empty project already exists at {root}. Pick another name.",
                speech=f"A project named {name} already exists, so I didn't touch it.",
            )

        created: list[str] = []
        root.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            created.append(rel)
        return root, created

    async def _run(self, name: str = "", template: str = "python", **kwargs: Any) -> dict[str, Any]:
        if not name or not name.strip():
            raise ToolError("A project name is required.", speech="What should I call the project?")

        root, created = await self.to_thread(self._scaffold, name.strip(), template)
        logger.info("Scaffolded %s project at %s (%d files)", template, root, len(created))
        return {
            "path": str(root),
            "name": name.strip(),
            "template": (template or "python").strip().lower(),
            "files": sorted(created),
            "file_count": len(created),
            "speech": f"Created a {template} project called {name} with {len(created)} files.",
            "display": f"Scaffolded {root}:\n" + "\n".join(f"  {f}" for f in sorted(created)),
            "artifacts": [str(root / f) for f in sorted(created)],
        }


class RunPythonTool(BaseTool):
    """Run a Python file from the projects folder, or a short inline snippet.

    The code runs under ``sys.executable`` in a fresh subprocess with **no
    shell**, ``cwd`` set to the script's project directory, a hard
    :data:`RUN_TIMEOUT_SECONDS` wall clock, and stdout/stderr captured and
    capped at :data:`OUTPUT_CAP_CHARS` characters each. Inline snippets are
    written to a temp file under ``projects_dir()/.snippets`` first, then
    removed after the run.
    """

    name = "run_python"
    description = "Execute a Python file from the projects folder, or a short code snippet."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.CODE
    aliases = ("execute_python", "run_script")
    mutating = True
    examples = (
        ToolExample(
            utterance="run main.py in my budget-tracker project",
            arguments={"file": "budget-tracker/main.py"},
        ),
        ToolExample(
            utterance="run this snippet: print(2 ** 10)",
            arguments={"code": "print(2 ** 10)"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "file": {
                "type": "string",
                "description": "Path to a .py file under the projects folder (relative or absolute).",
            },
            "code": {"type": "string", "description": "Short Python snippet to run instead of a file."},
            "timeout_seconds": {
                "type": "number",
                "description": f"Wall-clock limit in seconds (1–{int(RUN_TIMEOUT_SECONDS)}, default "
                               f"{int(RUN_TIMEOUT_SECONDS)}).",
            },
        },
        required=[],
    )

    def _resolve_script(self, file: str) -> Path:
        """Resolve a script path and ensure it lives under the projects dir."""
        projects_root = paths.projects_dir().resolve()
        candidate = Path(file)
        if not candidate.is_absolute():
            candidate = projects_root / file
        try:
            resolved = default_path_sandbox.resolve(candidate, must_exist=True)
        except SandboxError as exc:
            raise ToolError(str(exc), speech="That file is outside my allowed workspace.") from exc
        except FileNotFoundError as exc:
            raise ToolError(str(exc), speech="I couldn't find that file.") from exc
        try:
            resolved.relative_to(projects_root)
        except ValueError:
            raise ToolError(
                f"'{resolved}' is not inside the projects folder ({projects_root}); "
                "I only run code from there.",
                speech="I only run scripts that live in the projects folder.",
            ) from None
        if resolved.is_dir():
            raise ToolError(f"'{resolved}' is a directory, not a Python file.",
                            speech="That's a folder, not a script.")
        return resolved

    def _execute(self, script: Path, cwd: Path, timeout: float, cleanup: bool) -> dict[str, Any]:
        argv = [sys.executable, str(script)]
        timed_out = False
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) \
                else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) \
                else (exc.stderr or "")
            exit_code = -1
        finally:
            if cleanup:
                try:
                    script.unlink(missing_ok=True)
                except OSError:  # pragma: no cover - best-effort cleanup
                    pass

        def _cap(text: str) -> tuple[str, bool]:
            if len(text) > OUTPUT_CAP_CHARS:
                return text[:OUTPUT_CAP_CHARS] + "\n… [output truncated]", True
            return text, False

        stdout, out_trunc = _cap(stdout)
        stderr, err_trunc = _cap(stderr)
        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "output_truncated": out_trunc or err_trunc,
        }

    async def _run(
        self,
        file: str | None = None,
        code: str | None = None,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if bool(file) == bool(code):
            raise ToolError(
                "Provide exactly one of 'file' (a script under the projects folder) or 'code' "
                "(a short snippet).",
                speech="Tell me either a script to run or a snippet, not both.",
            )

        timeout = RUN_TIMEOUT_SECONDS
        if timeout_seconds is not None:
            timeout = max(1.0, min(float(timeout_seconds), RUN_TIMEOUT_SECONDS))

        cleanup = False
        if file:
            script = await self.to_thread(self._resolve_script, file)
            cwd = script.parent
            label = script.name
        else:
            assert code is not None
            if len(code) > MAX_SNIPPET_CHARS:
                raise ToolError(
                    f"The snippet is {len(code)} characters; the limit is {MAX_SNIPPET_CHARS}.",
                    speech="That snippet is too long — save it as a file instead.",
                )
            try:
                snippets_dir = default_path_sandbox.resolve(paths.projects_dir() / ".snippets")
            except SandboxError as exc:
                raise ToolError(str(exc), speech="The projects folder isn't accessible.") from exc
            snippets_dir.mkdir(parents=True, exist_ok=True)
            script = snippets_dir / f"snippet_{uuid.uuid4().hex[:12]}.py"
            await self.to_thread(script.write_text, code, "utf-8")
            cwd = snippets_dir
            label = "the snippet"
            cleanup = True

        report = await self.to_thread(self._execute, script, cwd, timeout, cleanup)
        logger.info("run_python %s exit=%s timed_out=%s", script, report["exit_code"], report["timed_out"])

        if report["timed_out"]:
            speech = f"I stopped {label} after {int(timeout)} seconds — it was still running."
        elif report["exit_code"] == 0:
            first_line = (report["stdout"].strip().splitlines() or [""])[0]
            speech = f"Ran {label} successfully." + (f" It printed: {first_line[:120]}" if first_line else "")
        else:
            speech = f"{label.capitalize()} exited with code {report['exit_code']}."

        display_parts = []
        if report["stdout"].strip():
            display_parts.append("stdout:\n" + report["stdout"].rstrip())
        if report["stderr"].strip():
            display_parts.append("stderr:\n" + report["stderr"].rstrip())
        return {
            **report,
            "script": str(script),
            "python": sys.executable,
            "speech": speech,
            "display": "\n\n".join(display_parts) or "(no output)",
        }


def get_tools() -> list[BaseTool]:
    return [WriteCodeTool(), ScaffoldProjectTool(), RunPythonTool()]
