"""Tests for the system tool modules (processes, shell, network).

Runs headless: process tests use the real ``psutil`` (a core dependency),
shell tests only ever execute ``echo``/``sleep``, and everything that would
touch the network (ping subprocesses, reachability probes, public IP lookup)
is monkeypatched. Pure helpers (critical-process screening, argv building,
host validation, ping output parsing) are tested directly.
"""

from __future__ import annotations

import json
import os
import subprocess

import psutil
import pytest

from iris.app.core.config import settings
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory
from iris.app.tools.base import ToolError
from iris.app.tools.system import network as network_mod
from iris.app.tools.system import processes as processes_mod
from iris.app.tools.system import shell as shell_mod
from iris.app.tools.system.network import (
    NetworkInfoTool,
    PingTool,
    build_ping_args,
    clamp_count,
    parse_ping_output,
    validate_host,
)
from iris.app.tools.system.processes import (
    CRITICAL_PROCESS_NAMES,
    KillProcessTool,
    ListProcessesTool,
    is_critical_process,
    normalize_process_name,
)
from iris.app.tools.system.shell import (
    EnvironmentInfoTool,
    ShellCommandTool,
    build_shell_argv,
    clamp_timeout,
    truncate_output,
)


# =============================================================================
# processes.py — pure helpers
# =============================================================================


def test_normalize_process_name_strips_exe_and_case():
    assert normalize_process_name("SVCHOST.EXE") == "svchost"
    assert normalize_process_name("  Explorer.exe ") == "explorer"
    assert normalize_process_name("systemd") == "systemd"
    assert normalize_process_name("") == ""


@pytest.mark.parametrize(
    "name",
    ["systemd", "init", "kernel_task", "wininit", "winlogon", "csrss", "services",
     "lsass", "smss", "explorer", "svchost", "dwm", "registry", "launchd",
     "WindowServer", "System", "iris", "uvicorn", "SVCHOST.EXE"],
)
def test_is_critical_process_names(name):
    assert is_critical_process(name) is True


def test_is_critical_process_own_pid_and_init():
    assert is_critical_process("python3", pid=os.getpid()) is True
    assert is_critical_process(None, pid=1) is True
    assert is_critical_process(None, pid=0) is True


def test_is_critical_process_normal():
    assert is_critical_process("chrome") is False
    assert is_critical_process("sleep", pid=999_999_999) is False


# =============================================================================
# processes.py — kill_process refusals
# =============================================================================


async def test_kill_process_refuses_critical_name():
    tool = KillProcessTool()
    result = await tool.execute(name_or_pid="systemd")
    assert result.success is False
    assert "critical" in result.error.lower()


async def test_kill_process_refuses_critical_name_windows_style():
    tool = KillProcessTool()
    result = await tool.execute(name_or_pid="svchost.exe")
    assert result.success is False
    assert "critical" in result.error.lower()


async def test_kill_process_refuses_own_pid():
    tool = KillProcessTool()
    result = await tool.execute(name_or_pid=str(os.getpid()))
    assert result.success is False
    assert "protect" in result.error.lower() or "critical" in result.error.lower()


async def test_kill_process_refuses_pid_one():
    tool = KillProcessTool()
    result = await tool.execute(name_or_pid="1")
    assert result.success is False


async def test_kill_process_empty_target():
    tool = KillProcessTool()
    result = await tool.execute(name_or_pid="   ")
    assert result.success is False


async def test_kill_process_unknown_name():
    tool = KillProcessTool()
    result = await tool.execute(name_or_pid="definitely-not-a-real-process-xyz")
    assert result.success is False
    assert "no running process" in result.error.lower()


async def test_kill_process_unknown_pid():
    pid = 4_194_000
    while psutil.pid_exists(pid):  # pragma: no cover - extremely unlikely loop
        pid -= 1
    tool = KillProcessTool()
    result = await tool.execute(name_or_pid=str(pid))
    assert result.success is False
    assert str(pid) in result.error


async def test_kill_process_terminates_real_child():
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        tool = KillProcessTool()
        result = await tool.execute(name_or_pid=str(child.pid))
        assert result.success is True, result.error
        outcomes = result.result["results"]
        assert len(outcomes) == 1
        assert outcomes[0]["pid"] == child.pid
        assert outcomes[0]["outcome"] in ("terminated", "force-killed")
        assert child.wait(timeout=5) != 0
    finally:
        if child.poll() is None:  # pragma: no cover - cleanup on failure
            child.kill()
            child.wait(timeout=5)


# =============================================================================
# processes.py — list_processes against the real psutil
# =============================================================================


async def test_list_processes_basic():
    tool = ListProcessesTool()
    result = await tool.execute(limit=5)
    assert result.success is True, result.error
    payload = result.result
    assert 0 < payload["count"] <= 5
    assert len(payload["processes"]) == payload["count"]
    first = payload["processes"][0]
    assert {"pid", "name", "cpu_percent", "memory_percent", "username"} <= set(first)
    totals = payload["totals"]
    assert totals["memory_total_bytes"] > 0
    assert 0.0 <= totals["memory_percent"] <= 100.0
    assert "PID" in result.display
    assert result.speech


async def test_list_processes_sorted_by_memory_and_capped():
    tool = ListProcessesTool()
    result = await tool.execute(sort_by="memory", limit=999)
    assert result.success is True, result.error
    rows = result.result["processes"]
    assert len(rows) <= 50  # cap applies
    values = [row["memory_percent"] for row in rows]
    assert values == sorted(values, reverse=True)


async def test_list_processes_filter_matches_python():
    tool = ListProcessesTool()
    result = await tool.execute(filter="py")
    assert result.success is True, result.error
    for row in result.result["processes"]:
        assert "py" in row["name"].lower()


async def test_list_processes_filter_no_match_is_clean():
    tool = ListProcessesTool()
    result = await tool.execute(filter="zz-no-such-process-qq")
    assert result.success is True, result.error
    assert result.result["count"] == 0
    assert result.result["processes"] == []


async def test_list_processes_rejects_bad_sort_key():
    tool = ListProcessesTool()
    result = await tool.execute(sort_by="alphabetical")
    assert result.success is False
    assert "cpu" in result.error.lower()


# =============================================================================
# shell.py — pure helpers
# =============================================================================


def test_build_shell_argv_posix_tokenizes():
    assert build_shell_argv("git status", windows=False) == ["git", "status"]
    assert build_shell_argv('echo "hello world"', windows=False) == ["echo", "hello world"]


def test_build_shell_argv_posix_metachars_are_literal():
    argv = build_shell_argv("echo a | b", windows=False)
    assert argv == ["echo", "a", "|", "b"]  # no shell: '|' is just an argument


def test_build_shell_argv_windows_uses_cmd_slash_c():
    assert build_shell_argv("dir C:\\", windows=True) == ["cmd", "/c", "dir C:\\"]


def test_build_shell_argv_rejects_empty_and_unbalanced():
    with pytest.raises(ToolError):
        build_shell_argv("   ", windows=False)
    with pytest.raises(ToolError):
        build_shell_argv('echo "unterminated', windows=False)


def test_clamp_timeout_bounds():
    assert clamp_timeout(None) == 30.0
    assert clamp_timeout("nope") == 30.0
    assert clamp_timeout(0) == 1.0
    assert clamp_timeout(999) == 120.0
    assert clamp_timeout(45) == 45.0


def test_truncate_output_caps_and_marks():
    text = "x" * 25_000
    capped = truncate_output(text)
    assert len(capped) < 25_000
    assert "truncated" in capped


# =============================================================================
# shell.py — run_command policy integration
# =============================================================================


async def test_run_command_refused_when_shell_tool_disabled():
    assert settings.ALLOW_SHELL_TOOL is False  # repo default
    tool = ShellCommandTool()
    result = await tool.execute(command="echo hello")
    assert result.success is False
    assert "disabled" in result.error.lower()


async def test_run_command_allowlisted_echo_runs(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    result = await tool.execute(command="echo hello world")
    assert result.success is True, result.error
    payload = result.result
    assert payload["exit_code"] == 0
    assert "hello world" in payload["stdout"]
    assert payload["argv"] == ["echo", "hello", "world"]


async def test_run_command_destructive_stays_refused(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    result = await tool.execute(command="rm -rf /")
    assert result.success is False
    assert "destructive" in result.error.lower()


async def test_run_command_destructive_refused_even_when_chained(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    result = await tool.execute(command="echo hi && rm -rf /home")
    assert result.success is False
    assert "destructive" in result.error.lower()


async def test_run_command_metachars_not_interpreted(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    # Without a shell, `;` and `whoami` are literal echo arguments.
    result = await tool.execute(command="echo safe ; whoami")
    assert result.success is True, result.error
    assert "safe ; whoami" in result.result["stdout"]


async def test_run_command_missing_program(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    result = await tool.execute(command="definitely-not-a-binary-xyz --version")
    assert result.success is False
    assert "not found" in result.error.lower()


async def test_run_command_timeout_kills(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    result = await tool.execute(command="sleep 5", timeout=1)
    assert result.success is False
    assert "timed out" in result.error.lower()


async def test_run_command_working_dir_outside_sandbox(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    result = await tool.execute(command="echo hi", working_dir="/etc")
    assert result.success is False
    assert result.error


async def test_run_command_nonzero_exit_is_reported_not_error(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SHELL_TOOL", True)
    tool = ShellCommandTool()
    result = await tool.execute(command="false")
    assert result.success is True  # the command ran; failing is information
    assert result.result["exit_code"] != 0
    assert "exit code" in result.speech


# =============================================================================
# shell.py — environment_info
# =============================================================================


async def test_environment_info_names_only():
    tool = EnvironmentInfoTool()
    result = await tool.execute()
    assert result.success is True, result.error
    payload = result.result
    assert payload["python_version"].count(".") == 2
    assert payload["path_dir_count"] > 0
    assert "PATH" in payload["env_var_names"]
    assert payload["cwd"]
    assert payload["user"]
    # No env var *values* may ever leak into the result.
    dumped = json.dumps(payload)
    assert os.environ["PATH"] not in dumped


# =============================================================================
# network.py — pure helpers
# =============================================================================


@pytest.mark.parametrize(
    "host",
    ["google.com", "sub.domain.example.co.uk", "8.8.8.8", "192.168.1.1",
     "::1", "2606:4700:4700::1111", "nas-01.local", "localhost"],
)
def test_validate_host_accepts(host):
    assert validate_host(host)


@pytest.mark.parametrize(
    "host",
    ["", "   ", "evil.com; rm -rf /", "host name", "a|b", "$(whoami)",
     "`id`", "host&", "http://x.com/path", "-flag.example.com", "a..b"],
)
def test_validate_host_rejects(host):
    with pytest.raises(ToolError):
        validate_host(host)


def test_build_ping_args_posix_and_windows():
    assert build_ping_args("8.8.8.8", 4, windows=False) == ["ping", "-c", "4", "8.8.8.8"]
    assert build_ping_args("8.8.8.8", 2, windows=True) == ["ping", "-n", "2", "8.8.8.8"]


def test_clamp_count_bounds():
    assert clamp_count(None) == 4
    assert clamp_count(0) == 1
    assert clamp_count(99) == 10
    assert clamp_count(6) == 6


LINUX_PING_OUTPUT = """\
PING google.com (142.250.72.14) 56(84) bytes of data.
64 bytes from lax.net (142.250.72.14): icmp_seq=1 ttl=115 time=14.3 ms
64 bytes from lax.net (142.250.72.14): icmp_seq=2 ttl=115 time=16.0 ms

--- google.com ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3004ms
rtt min/avg/max/mdev = 14.312/15.104/16.001/0.712 ms
"""

WINDOWS_PING_OUTPUT = """\
Pinging google.com [142.250.72.14] with 32 bytes of data:
Reply from 142.250.72.14: bytes=32 time=14ms TTL=115

Ping statistics for 142.250.72.14:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 14ms, Maximum = 16ms, Average = 15ms
"""


def test_parse_ping_output_linux():
    stats = parse_ping_output(LINUX_PING_OUTPUT)
    assert stats["avg_ms"] == pytest.approx(15.104)
    assert stats["packet_loss_percent"] == 0.0


def test_parse_ping_output_windows():
    stats = parse_ping_output(WINDOWS_PING_OUTPUT)
    assert stats["avg_ms"] == 15.0
    assert stats["packet_loss_percent"] == 0.0


def test_parse_ping_output_unparseable():
    stats = parse_ping_output("ping: unknown host nope.invalid")
    assert stats["avg_ms"] is None
    assert stats["packet_loss_percent"] is None


# =============================================================================
# network.py — tools (network faked out)
# =============================================================================


async def test_ping_tool_rejects_bad_host():
    tool = PingTool()
    result = await tool.execute(host="google.com; cat /etc/passwd")
    assert result.success is False
    assert "valid" in result.error.lower()


async def test_ping_tool_parses_fake_run(monkeypatch):
    captured: dict[str, list[str]] = {}

    async def fake_run(argv, timeout=15.0):
        captured["argv"] = argv
        return 0, LINUX_PING_OUTPUT

    monkeypatch.setattr(network_mod, "_run_ping_process", fake_run)
    monkeypatch.setattr(network_mod, "has_binary", lambda name: True)

    tool = PingTool()
    result = await tool.execute(host="google.com", count=99)
    assert result.success is True, result.error
    assert captured["argv"][0] == "ping"
    assert captured["argv"][-1] == "google.com"
    assert "10" in captured["argv"]  # count capped at 10
    payload = result.result
    assert payload["reachable"] is True
    assert payload["avg_ms"] == pytest.approx(15.104)
    assert "reachable" in result.speech


async def test_ping_tool_unreachable_host(monkeypatch):
    async def fake_run(argv, timeout=15.0):
        return 1, "4 packets transmitted, 0 received, 100% packet loss, time 3062ms"

    monkeypatch.setattr(network_mod, "_run_ping_process", fake_run)
    monkeypatch.setattr(network_mod, "has_binary", lambda name: True)

    tool = PingTool()
    result = await tool.execute(host="10.255.255.1", count=4)
    assert result.success is True, result.error
    assert result.result["reachable"] is False
    assert result.result["packet_loss_percent"] == 100.0
    assert "did not respond" in result.speech


async def test_network_info_local_only(monkeypatch):
    monkeypatch.setattr(network_mod, "_check_internet", lambda *a, **k: True)
    monkeypatch.setattr(network_mod, "_primary_local_ip", lambda: "10.0.0.5")

    tool = NetworkInfoTool()
    result = await tool.execute()
    assert result.success is True, result.error
    payload = result.result
    assert payload["primary_ip"] == "10.0.0.5"
    assert payload["internet_reachable"] is True
    assert isinstance(payload["interfaces"], dict)
    assert "127." not in json.dumps(payload["interfaces"])
    assert "public_ip" not in payload  # opt-in only
    assert "10.0.0.5" in result.speech


async def test_network_info_public_opt_in(monkeypatch):
    monkeypatch.setattr(network_mod, "_check_internet", lambda *a, **k: True)
    monkeypatch.setattr(network_mod, "_primary_local_ip", lambda: "10.0.0.5")

    async def fake_public():
        return "203.0.113.7"

    monkeypatch.setattr(network_mod, "_fetch_public_ip", fake_public)

    tool = NetworkInfoTool()
    result = await tool.execute(public=True)
    assert result.success is True, result.error
    assert result.result["public_ip"] == "203.0.113.7"
    assert "203.0.113.7" in result.speech


async def test_network_info_public_lookup_failure_is_soft(monkeypatch):
    monkeypatch.setattr(network_mod, "_check_internet", lambda *a, **k: False)
    monkeypatch.setattr(network_mod, "_primary_local_ip", lambda: None)

    async def fake_public():
        raise ToolError("Couldn't look up the public IP: offline")

    monkeypatch.setattr(network_mod, "_fetch_public_ip", fake_public)

    tool = NetworkInfoTool()
    result = await tool.execute(public=True)
    assert result.success is True, result.error  # local info still useful
    assert result.result["public_ip"] is None
    assert "public_ip_error" in result.result


# =============================================================================
# module metadata & factories
# =============================================================================


def test_get_tools_factories_and_metadata():
    proc_tools = {t.name: t for t in processes_mod.get_tools()}
    shell_tools = {t.name: t for t in shell_mod.get_tools()}
    net_tools = {t.name: t for t in network_mod.get_tools()}

    assert set(proc_tools) == {"list_processes", "kill_process"}
    assert set(shell_tools) == {"run_command", "environment_info"}
    assert set(net_tools) == {"network_info", "ping"}

    for tool in [*proc_tools.values(), *shell_tools.values(), *net_tools.values()]:
        assert tool.category == ToolCategory.SYSTEM
        assert tool.description
        assert tool.examples
        assert tool.get_metadata()  # schema serializes cleanly

    assert proc_tools["list_processes"].permission_level == PermissionLevel.READ
    assert "running_apps" in proc_tools["list_processes"].aliases
    assert proc_tools["kill_process"].permission_level == PermissionLevel.CONFIRM_REQUIRED
    assert proc_tools["kill_process"].mutating is True
    assert "end_task" in proc_tools["kill_process"].aliases

    assert shell_tools["run_command"].permission_level == PermissionLevel.HIGH_RISK_ACTION
    assert shell_tools["run_command"].mutating is True
    assert "cmd" in shell_tools["run_command"].aliases
    assert shell_tools["environment_info"].permission_level == PermissionLevel.READ

    assert net_tools["network_info"].network is True
    assert "my_ip" in net_tools["network_info"].aliases
    assert net_tools["ping"].permission_level == PermissionLevel.NETWORK_ACTION
    assert net_tools["ping"].network is True


def test_critical_set_contains_spec_names():
    for name in ("system", "systemd", "init", "kernel_task", "wininit", "winlogon",
                 "csrss", "services", "lsass", "smss", "explorer", "svchost",
                 "dwm", "registry", "launchd", "windowserver"):
        assert name in CRITICAL_PROCESS_NAMES
