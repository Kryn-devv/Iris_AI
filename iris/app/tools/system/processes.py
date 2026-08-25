"""Process management tools: list running processes and stop misbehaving ones.

* :class:`ListProcessesTool` — "what's eating my CPU?", "show running apps"
* :class:`KillProcessTool` — "kill chrome", "end task 4312"

Safety properties:

* Killing is gated behind ``CONFIRM_REQUIRED`` so the permission layer always
  asks the user first.
* A static set of critical system processes (:data:`CRITICAL_PROCESS_NAMES`)
  and the IRIS server's own process (``os.getpid()``, plus PIDs 0 and 1) can
  never be targeted — the request is refused with a clear explanation before
  a single signal is sent. The pure helper :func:`is_critical_process` makes
  this decision testable in isolation.
* Termination is graceful-first: ``terminate()`` (SIGTERM), a 3 second grace
  period, then ``kill()`` (SIGKILL) only for survivors, with a per-process
  outcome report.

Both tools rely on ``psutil``. It ships in the core requirements, but it is
still loaded through :func:`try_import` so a broken install degrades to a
clean error instead of an ``ImportError`` at boot.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import try_import
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

logger = get_logger("tools.system.processes")

__all__ = [
    "CRITICAL_PROCESS_NAMES",
    "normalize_process_name",
    "is_critical_process",
    "ListProcessesTool",
    "KillProcessTool",
    "get_tools",
]

#: Sort keys accepted by :class:`ListProcessesTool` (aliases -> canonical).
_SORT_KEYS: dict[str, str] = {
    "cpu": "cpu",
    "cpu_percent": "cpu",
    "processor": "cpu",
    "memory": "memory",
    "mem": "memory",
    "ram": "memory",
    "memory_percent": "memory",
}

#: Default and maximum number of rows returned by ``list_processes``.
DEFAULT_LIMIT = 10
MAX_LIMIT = 50

#: Seconds between the two CPU sampling passes in ``list_processes``.
_CPU_SAMPLE_INTERVAL = 0.25

#: Grace period (seconds) between ``terminate()`` and ``kill()``.
_TERMINATE_GRACE = 3.0

#: Upper bound on how many processes one ``kill_process`` call may target.
_MAX_KILL_TARGETS = 25

#: Process names (lowercased, ``.exe`` stripped) that IRIS refuses to kill.
#: Killing any of these bricks the session or the whole OS: Windows session
#: managers (``wininit``/``winlogon``/``csrss``/``smss``), service hosts
#: (``services``/``svchost``/``lsass``), shells and compositors
#: (``explorer``/``dwm``/``windowserver``), init systems (``init``/
#: ``systemd``/``launchd``), kernel bookkeeping (``system``/``kernel_task``/
#: ``registry``) and IRIS itself (``iris``/``uvicorn``).
CRITICAL_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "system",
        "system idle process",
        "systemd",
        "init",
        "kernel_task",
        "wininit",
        "winlogon",
        "csrss",
        "services",
        "lsass",
        "smss",
        "explorer",
        "svchost",
        "dwm",
        "registry",
        "launchd",
        "windowserver",
        "iris",
        "uvicorn",
    }
)


# =============================================================================
# Pure helpers
# =============================================================================


def normalize_process_name(name: str) -> str:
    """Canonical comparison form of a process name.

    Lowercases, trims whitespace and strips a trailing ``.exe`` so that
    ``"SVCHOST.EXE"`` and ``"svchost"`` compare equal across platforms.
    """
    text = (name or "").strip().lower()
    return text[:-4] if text.endswith(".exe") else text


def is_critical_process(name: str | None, pid: int | None = None) -> bool:
    """True when a process must never be killed by IRIS.

    Protects everything in :data:`CRITICAL_PROCESS_NAMES` by name, plus PID 0,
    PID 1 (the init process, whatever it is named inside a container) and the
    IRIS server's own PID so the assistant cannot terminate itself mid-answer.
    """
    if pid is not None and pid in (0, 1, os.getpid()):
        return True
    return normalize_process_name(name or "") in CRITICAL_PROCESS_NAMES


def _clamp_limit(raw: Any) -> int:
    """Coerce the ``limit`` argument into ``1..MAX_LIMIT``."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _fmt_mb(num_bytes: float) -> str:
    """Human-readable size, GB above 1 GiB and MB below."""
    gib = num_bytes / (1024**3)
    if gib >= 1.0:
        return f"{gib:.1f} GB"
    return f"{num_bytes / (1024 ** 2):.0f} MB"


def _format_process_table(rows: list[dict[str, Any]]) -> str:
    """Render process rows as a fixed-width, table-ish block of text."""
    lines = [f"{'PID':>7}  {'NAME':<28} {'CPU%':>6} {'MEM%':>6}  USER"]
    for row in rows:
        name = str(row.get("name") or "?")
        if len(name) > 28:
            name = name[:27] + "…"
        lines.append(
            f"{row.get('pid', 0):>7}  {name:<28} "
            f"{row.get('cpu_percent', 0.0):>6.1f} {row.get('memory_percent', 0.0):>6.1f}  "
            f"{row.get('username') or '-'}"
        )
    return "\n".join(lines)


# =============================================================================
# Blocking psutil workers (always run via ``to_thread``)
# =============================================================================

_PROC_ATTRS = ["pid", "name", "cpu_percent", "memory_percent", "username"]


def _collect_processes(
    psutil_mod: Any,
    sort_by: str,
    limit: int,
    name_filter: str | None,
) -> dict[str, Any]:
    """Snapshot running processes, sorted and trimmed. Blocking (~0.3 s)."""
    # First pass primes psutil's per-process CPU counters (they measure the
    # delta since the previous call, so the very first reading is always 0.0).
    for _ in psutil_mod.process_iter(_PROC_ATTRS):
        pass
    psutil_mod.cpu_percent(interval=None)
    time.sleep(_CPU_SAMPLE_INTERVAL)

    needle = (name_filter or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for proc in psutil_mod.process_iter(_PROC_ATTRS):
        try:
            info = proc.info
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):  # pragma: no cover
            continue
        name = str(info.get("name") or "?")
        if needle and needle not in name.lower():
            continue
        rows.append(
            {
                "pid": int(info.get("pid") or 0),
                "name": name,
                "cpu_percent": round(float(info.get("cpu_percent") or 0.0), 1),
                "memory_percent": round(float(info.get("memory_percent") or 0.0), 1),
                "username": info.get("username"),
            }
        )

    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    rows.sort(key=lambda row: (row[key], row["cpu_percent"]), reverse=True)

    memory = psutil_mod.virtual_memory()
    return {
        "rows": rows[:limit],
        "total_matching": len(rows),
        "cpu_total_percent": round(float(psutil_mod.cpu_percent(interval=None)), 1),
        "memory_used_bytes": int(memory.used),
        "memory_total_bytes": int(memory.total),
        "memory_percent": round(float(memory.percent), 1),
    }


def _resolve_kill_targets(psutil_mod: Any, name_or_pid: str) -> list[Any]:
    """Resolve a PID string or a process name into live ``psutil.Process`` objects.

    Name resolution prefers exact (normalized) matches and only falls back to
    substring matching when nothing matches exactly, so ``kill "sh"`` cannot
    silently sweep up ``ssh-agent`` and friends.
    """
    text = str(name_or_pid).strip()
    if re.fullmatch(r"\d+", text):
        pid = int(text)
        try:
            return [psutil_mod.Process(pid)]
        except psutil_mod.NoSuchProcess:
            raise ToolError(
                f"No process with PID {pid} is running.",
                speech=f"I couldn't find a process with PID {pid}.",
            ) from None

    target = normalize_process_name(text)
    exact: list[Any] = []
    fuzzy: list[Any] = []
    for proc in psutil_mod.process_iter(["pid", "name"]):
        try:
            name = normalize_process_name(str(proc.info.get("name") or ""))
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):  # pragma: no cover
            continue
        if name == target:
            exact.append(proc)
        elif target and target in name:
            fuzzy.append(proc)

    matches = exact or fuzzy
    if not matches:
        raise ToolError(
            f"No running process matches '{text}'.",
            speech=f"I couldn't find any process called {text}.",
        )
    if len(matches) > _MAX_KILL_TARGETS:
        raise ToolError(
            f"'{text}' matches {len(matches)} processes — that's too broad. "
            "Give me an exact name or a PID.",
            speech=f"That matches {len(matches)} processes, which is too many. "
            "Please be more specific.",
        )
    return matches


def _terminate_processes(psutil_mod: Any, procs: list[Any]) -> list[dict[str, Any]]:
    """Terminate-then-kill each process, returning per-process outcomes. Blocking."""
    outcomes: dict[int, dict[str, Any]] = {}
    signalled: list[Any] = []

    for proc in procs:
        entry = {"pid": proc.pid, "name": "?", "outcome": ""}
        try:
            entry["name"] = proc.name()
            proc.terminate()
            signalled.append(proc)
        except psutil_mod.NoSuchProcess:
            entry["outcome"] = "already exited"
        except psutil_mod.AccessDenied:
            entry["outcome"] = "failed: access denied (try running IRIS with more privileges)"
        except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
            entry["outcome"] = f"failed: {exc}"
        outcomes[proc.pid] = entry

    if signalled:
        gone, alive = psutil_mod.wait_procs(signalled, timeout=_TERMINATE_GRACE)
        for proc in gone:
            outcomes[proc.pid]["outcome"] = "terminated"
        for proc in alive:
            try:
                proc.kill()
            except psutil_mod.NoSuchProcess:
                outcomes[proc.pid]["outcome"] = "terminated"
                continue
            except Exception as exc:  # noqa: BLE001
                outcomes[proc.pid]["outcome"] = f"failed: {exc}"
                continue
            outcomes[proc.pid]["outcome"] = "force-killed"
        if alive:
            psutil_mod.wait_procs(alive, timeout=1.0)

    return [outcomes[proc.pid] for proc in procs]


# =============================================================================
# Tools
# =============================================================================


class ListProcessesTool(BaseTool):
    """List running processes sorted by CPU or memory usage."""

    name = "list_processes"
    description = "Lists running processes sorted by CPU or memory usage, with system totals."
    permission_level = PermissionLevel.READ
    category = ToolCategory.SYSTEM
    aliases = ("show_processes", "task_list", "running_apps")
    examples = (
        ToolExample(utterance="what's eating my CPU?", arguments={"sort_by": "cpu"}),
        ToolExample(
            utterance="show the top 5 memory hogs",
            arguments={"sort_by": "memory", "limit": 5},
        ),
        ToolExample(
            utterance="is chrome running?",
            arguments={"filter": "chrome"},
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "sort_by": {
                "type": "string",
                "enum": ["cpu", "memory"],
                "description": "Sort order: 'cpu' (default) or 'memory'.",
            },
            "limit": {
                "type": "integer",
                "description": f"Rows to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
            },
            "filter": {
                "type": "string",
                "description": "Only include processes whose name contains this substring.",
            },
        },
        required=[],
    )

    async def _run(
        self,
        sort_by: str = "cpu",
        limit: int = DEFAULT_LIMIT,
        filter: str | None = None,  # noqa: A002 - external argument name
        **kwargs: Any,
    ) -> dict[str, Any]:
        psutil_mod = try_import("psutil")
        if psutil_mod is None:
            raise ToolError(
                "The 'psutil' package is not installed. Install with: pip install psutil",
                speech="I can't inspect processes because psutil isn't installed.",
            )

        sort_key = _SORT_KEYS.get((sort_by or "cpu").strip().lower())
        if sort_key is None:
            raise ToolError(
                f"Unknown sort key '{sort_by}'. Use 'cpu' or 'memory'.",
                speech="I can sort processes by CPU or by memory.",
            )
        capped_limit = _clamp_limit(limit)

        snapshot = await self.to_thread(
            _collect_processes, psutil_mod, sort_key, capped_limit, filter
        )
        rows = snapshot["rows"]

        used = _fmt_mb(snapshot["memory_used_bytes"])
        total = _fmt_mb(snapshot["memory_total_bytes"])
        totals_line = (
            f"CPU {snapshot['cpu_total_percent']:.1f}% | "
            f"Memory {used} / {total} ({snapshot['memory_percent']:.1f}%)"
        )

        if not rows:
            speech = (
                f"No running processes match '{filter}'."
                if filter
                else "I couldn't see any running processes."
            )
            display = f"No matching processes.\n{totals_line}"
        else:
            top = rows[0]
            metric = (
                f"{top['cpu_percent']:.1f} percent CPU"
                if sort_key == "cpu"
                else f"{top['memory_percent']:.1f} percent memory"
            )
            speech = (
                f"Top {len(rows)} of {snapshot['total_matching']} processes by "
                f"{'CPU' if sort_key == 'cpu' else 'memory'}: {top['name']} leads at {metric}."
            )
            display = _format_process_table(rows) + "\n\n" + totals_line

        return {
            "processes": rows,
            "count": len(rows),
            "total_matching": snapshot["total_matching"],
            "sort_by": sort_key,
            "filter": filter,
            "totals": {
                "cpu_percent": snapshot["cpu_total_percent"],
                "memory_used": used,
                "memory_total": total,
                "memory_used_bytes": snapshot["memory_used_bytes"],
                "memory_total_bytes": snapshot["memory_total_bytes"],
                "memory_percent": snapshot["memory_percent"],
            },
            "speech": speech,
            "display": display,
        }


class KillProcessTool(BaseTool):
    """Terminate a process by name or PID, with critical-process protection."""

    name = "kill_process"
    description = "Stops a running process by name or PID (asks for confirmation first)."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.SYSTEM
    aliases = ("end_task", "stop_process")
    mutating = True
    examples = (
        ToolExample(utterance="kill chrome", arguments={"name_or_pid": "chrome"}),
        ToolExample(utterance="end task 4312", arguments={"name_or_pid": "4312"}),
        ToolExample(
            utterance="stop the notepad process", arguments={"name_or_pid": "notepad"}
        ),
    )
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "name_or_pid": {
                "type": "string",
                "description": "Process name (e.g. 'chrome') or numeric PID (e.g. '4312').",
            },
        },
        required=["name_or_pid"],
    )

    async def _run(self, name_or_pid: str = "", **kwargs: Any) -> dict[str, Any]:
        text = str(name_or_pid).strip()
        if not text:
            raise ToolError(
                "Tell me which process to stop — a name or a PID.",
                speech="Which process should I stop?",
            )

        # Refuse critical *requests* immediately — even before checking whether
        # such a process is running ("kill systemd" is never a good idea).
        if not text.isdigit() and is_critical_process(text):
            raise ToolError(
                f"'{text}' is a critical system process. Killing it could crash "
                "your session or the whole machine, so I won't touch it.",
                speech=f"I can't kill {text} — it's a critical system process.",
            )

        psutil_mod = try_import("psutil")
        if psutil_mod is None:
            raise ToolError(
                "The 'psutil' package is not installed. Install with: pip install psutil",
                speech="I can't manage processes because psutil isn't installed.",
            )

        # Own-PID / PID 0-1 protection before we even look the process up.
        if text.isdigit() and is_critical_process(None, pid=int(text)):
            raise ToolError(
                f"PID {text} is protected: it is the IRIS assistant itself or the "
                "system init process. I won't terminate it.",
                speech="I can't kill that one — it would take me down with it.",
            )

        targets = await self.to_thread(_resolve_kill_targets, psutil_mod, text)

        # Screen every resolved target: a PID may point at a critical process,
        # and a fuzzy name match may have swept one up.
        for proc in targets:
            try:
                proc_name = proc.name()
            except Exception:  # noqa: BLE001 - a vanished process is fine here
                proc_name = ""
            if is_critical_process(proc_name, pid=proc.pid):
                raise ToolError(
                    f"'{proc_name or text}' (PID {proc.pid}) is a critical system "
                    "process. Killing it could crash your session or the whole "
                    "machine, so I won't touch it.",
                    speech=f"I can't kill {proc_name or text} — it's a critical system process.",
                )

        outcomes = await self.to_thread(_terminate_processes, psutil_mod, targets)

        stopped = [o for o in outcomes if o["outcome"] in ("terminated", "force-killed", "already exited")]
        failed = [o for o in outcomes if o["outcome"].startswith("failed")]

        lines = [f"PID {o['pid']} ({o['name']}): {o['outcome']}" for o in outcomes]
        if not stopped:
            raise ToolError(
                "Couldn't stop the process: " + "; ".join(lines),
                speech=f"I wasn't able to stop {text}.",
            )

        if failed:
            speech = f"Stopped {len(stopped)} of {len(outcomes)} matching processes."
        elif len(outcomes) == 1:
            speech = f"Stopped {outcomes[0]['name']} (PID {outcomes[0]['pid']})."
        else:
            speech = f"Stopped all {len(outcomes)} processes matching {text}."

        logger.info("kill_process '%s': %s", text, "; ".join(lines))
        return {
            "target": text,
            "results": outcomes,
            "stopped": len(stopped),
            "failed": len(failed),
            "speech": speech,
            "display": "\n".join(lines),
        }


def get_tools() -> list[BaseTool]:
    return [ListProcessesTool(), KillProcessTool()]
