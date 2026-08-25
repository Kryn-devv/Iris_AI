"""Shell command execution and environment inspection tools.

* :class:`ShellCommandTool` — "run `git status` in my project folder"
* :class:`EnvironmentInfoTool` — "what Python am I on?", "what's my PATH like?"

Safety properties (the shell tool is the sharpest knife in IRIS):

* Every command is screened by
  :data:`iris.app.core.security.default_command_policy` **before** anything is
  spawned. A denied verdict (shell tool disabled, destructive pattern, empty
  command) is refused with the policy's own reason. Risky-but-allowed
  commands are still executed here — requiring the user's confirmation is the
  permission layer's job, enforced through ``HIGH_RISK_ACTION``.
* Commands never pass through a shell: no ``create_subprocess_shell``, no
  ``shell=True``. On POSIX the command is tokenized with :func:`shlex.split`
  and executed directly, so ``&&``, ``|``, ``$(...)`` and backticks are inert
  literal arguments. On Windows the argv is ``["cmd", "/c", command]`` because
  most built-ins (``dir``, ``type``…) only exist inside ``cmd``.
* An optional working directory is resolved through the filesystem sandbox
  (:data:`iris.app.core.security.default_path_sandbox`) so a command cannot be
  anchored outside the allowed roots.
* Output is captured and capped at :data:`MAX_OUTPUT_CHARS` per stream, and a
  hard timeout (default 30 s, max 120 s) kills runaway commands.

:class:`EnvironmentInfoTool` deliberately reports environment variable *names*
only — values routinely contain tokens, keys and passwords, and none of that
should ever transit through a model or a TTS engine.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import platform
import shlex
import sys
import time
from typing import Any

from iris.app.core import security
from iris.app.core.logging import get_logger
from iris.app.core.platform_info import current_os, is_windows
from iris.app.core.security import PermissionLevel, SandboxError
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.system.shell")

__all__ = [
    "MAX_OUTPUT_CHARS",
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "build_shell_argv",
    "clamp_timeout",
    "truncate_output",
    "ShellCommandTool",
    "EnvironmentInfoTool",
    "get_tools",
]

#: Maximum characters kept per output stream (stdout / stderr).
MAX_OUTPUT_CHARS = 20_000
#: Default and maximum command timeout in seconds.
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0

#: Environment variable names considered interesting enough to report.
#: Only the *names* of the ones actually set are ever returned.
_KEY_ENV_VARS: tuple[str, ...] = (
    "PATH", "HOME", "USER", "USERNAME", "USERPROFILE", "SHELL", "COMSPEC",
    "LANG", "LC_ALL", "TERM", "EDITOR", "VISUAL", "PAGER",
    "TMPDIR", "TEMP", "TMP",
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_DATA_HOME",
    "VIRTUAL_ENV", "CONDA_DEFAULT_ENV", "PYTHONPATH", "PYTHONHOME",
    "JAVA_HOME", "GOPATH", "GOROOT", "CARGO_HOME", "NODE_ENV", "NVM_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSH_AUTH_SOCK",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "SYSTEMROOT",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
    "IRIS_DATA_DIR", "IRIS_CONFIG_DIR",
)


# =============================================================================
# Pure helpers
# =============================================================================


def build_shell_argv(command: str, *, windows: bool | None = None) -> list[str]:
    """Turn a command string into an argv list — never a shell string.

    On POSIX the command is tokenized with :func:`shlex.split`, so quoting
    works but shell metacharacters (``&&``, ``|``, ``$(...)``) become literal
    arguments with no special meaning. On Windows the whole command is handed
    to ``cmd /c`` because the built-ins users actually ask for (``dir``,
    ``type``, ``ver``) only exist inside ``cmd``.

    Raises :class:`ToolError` for empty or unparseable commands.
    """
    text = (command or "").strip()
    if not text:
        raise ToolError("The command is empty.", speech="There was no command to run.")

    if windows is None:
        windows = is_windows()
    if windows:
        return ["cmd", "/c", text]

    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ToolError(
            f"Couldn't parse the command ({exc}). Check for unbalanced quotes.",
            speech="I couldn't parse that command — check the quoting.",
        ) from exc
    if not argv:
        raise ToolError("The command is empty.", speech="There was no command to run.")
    return argv


def clamp_timeout(raw: Any) -> float:
    """Coerce the ``timeout`` argument into ``1..MAX_TIMEOUT`` seconds."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(1.0, min(value, MAX_TIMEOUT))


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cap a captured stream, marking how much was dropped."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return text[:limit] + f"\n… [truncated {dropped} characters]"


# =============================================================================
# Tools
# =============================================================================


class ShellCommandTool(BaseTool):
    """Run a screened terminal command without a shell interpreter."""

    name = "run_command"
    description = "Runs a terminal command (screened against the security policy) and returns its output."
    permission_level = PermissionLevel.HIGH_RISK_ACTION
    category = ToolCategory.SYSTEM
    aliases = ("shell", "terminal_command", "cmd")
    mutating = True
    examples = (
        ToolExample(utterance="run git status", arguments={"command": "git status"}),
        ToolExample(
            utterance="run npm install in my project folder",
            arguments={"command": "npm install", "working_dir": "~/Documents/myproject"},
        ),
        ToolExample(
            utterance="how much disk space is free?",
            arguments={"command": "df -h"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "command": {
                "type": "string",
                "description": "The command line to run, e.g. 'git status'.",
            },
            "working_dir": {
                "type": "string",
                "description": "Directory to run in (must be inside the allowed workspace).",
            },
            "timeout": {
                "type": "number",
                "description": f"Seconds before the command is killed (default {DEFAULT_TIMEOUT:.0f}, max {MAX_TIMEOUT:.0f}).",
            },
        },
        required=["command"],
    )

    async def _run(
        self,
        command: str = "",
        working_dir: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        **kwargs: Any,
    ) -> dict[str, Any]:
        text = (command or "").strip()

        # --- policy screen: this happens before anything else ---------------
        verdict = security.default_command_policy.screen(text)
        if not verdict.allowed:
            logger.warning("run_command refused (%s): %r", verdict.reason, text[:200])
            raise ToolError(verdict.reason, speech="I can't run that command.")

        # --- working directory through the filesystem sandbox ---------------
        cwd: str | None = None
        if working_dir:
            try:
                resolved = security.default_path_sandbox.resolve(working_dir, must_exist=True)
            except SandboxError as exc:
                raise ToolError(str(exc), speech="That folder is outside my allowed workspace.") from exc
            except FileNotFoundError as exc:
                raise ToolError(str(exc), speech="That folder doesn't exist.") from exc
            if not resolved.is_dir():
                raise ToolError(
                    f"'{resolved}' is not a directory.",
                    speech="That path isn't a folder.",
                )
            cwd = str(resolved)

        argv = build_shell_argv(text)
        seconds = clamp_timeout(timeout)

        logger.info("run_command executing %r (cwd=%s, timeout=%.0fs)", argv, cwd, seconds)
        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise ToolError(
                f"Program '{argv[0]}' was not found on this system.",
                speech=f"I couldn't find a program called {argv[0]}.",
            ) from None
        except PermissionError:
            raise ToolError(
                f"No permission to execute '{argv[0]}'.",
                speech="I don't have permission to run that program.",
            ) from None
        except NotADirectoryError:
            raise ToolError(
                f"Working directory '{cwd}' is invalid.",
                speech="That working folder isn't usable.",
            ) from None

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ToolError(
                f"Command timed out after {seconds:.0f} seconds and was killed.",
                speech="That command took too long, so I stopped it.",
            ) from None

        elapsed = time.perf_counter() - started
        stdout = truncate_output(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = truncate_output(stderr_bytes.decode("utf-8", errors="replace"))
        exit_code = proc.returncode if proc.returncode is not None else -1

        if exit_code == 0:
            speech = "The command finished successfully."
        else:
            speech = f"The command failed with exit code {exit_code}."

        display_parts = [f"$ {text}", f"(exit {exit_code}, {elapsed:.1f}s)"]
        if stdout.strip():
            display_parts.append(stdout.rstrip())
        if stderr.strip():
            display_parts.append("[stderr]\n" + stderr.rstrip())

        return {
            "command": text,
            "argv": argv,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "working_dir": cwd,
            "duration_seconds": round(elapsed, 2),
            "policy": verdict.to_dict(),
            "speech": speech,
            "display": "\n".join(display_parts),
        }


class EnvironmentInfoTool(BaseTool):
    """Report the runtime environment: Python, PATH, user, shell, cwd."""

    name = "environment_info"
    description = "Shows the runtime environment: Python version, PATH size, key env var names, user and shell."
    permission_level = PermissionLevel.READ
    category = ToolCategory.SYSTEM
    aliases = ("env_info", "python_version", "show_environment")
    examples = (
        ToolExample(utterance="what python version am I running?", arguments={}),
        ToolExample(utterance="show my environment info", arguments={}),
    )
    input_schema = ToolParameterSchema(type="object", properties={}, required=[])

    async def _run(self, **kwargs: Any) -> dict[str, Any]:
        path_dirs = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]
        env_var_names = sorted(name for name in _KEY_ENV_VARS if name in os.environ)

        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 - no passwd entry in some containers
            user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

        if is_windows():
            shell_path = os.environ.get("COMSPEC", "cmd.exe")
        else:
            shell_path = os.environ.get("SHELL", "")
        shell_name = os.path.basename(shell_path) if shell_path else "unknown"

        python_version = platform.python_version()
        info = {
            "python_version": python_version,
            "python_implementation": platform.python_implementation(),
            "python_executable": sys.executable,
            "os": current_os(),
            "path_dir_count": len(path_dirs),
            "env_var_count": len(os.environ),
            "env_var_names": env_var_names,
            "cwd": os.getcwd(),
            "user": user,
            "shell": shell_name,
        }
        info["speech"] = (
            f"You're running Python {python_version} as {user} with {shell_name}, "
            f"and your PATH has {len(path_dirs)} directories."
        )
        info["display"] = (
            f"Python:  {python_version} ({info['python_implementation']}) — {sys.executable}\n"
            f"OS:      {info['os']}\n"
            f"User:    {user}    Shell: {shell_name}\n"
            f"CWD:     {info['cwd']}\n"
            f"PATH:    {len(path_dirs)} directories\n"
            f"Env:     {len(os.environ)} variables set "
            f"(key names: {', '.join(env_var_names) or 'none'})"
        )
        return info


def get_tools() -> list[BaseTool]:
    return [ShellCommandTool(), EnvironmentInfoTool()]
