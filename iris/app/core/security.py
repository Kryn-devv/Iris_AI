"""Permission management, filesystem sandboxing and command policy for IRIS.

IRIS can drive the whole desktop, so the security layer is the most important
module in the project. Three independent guards compose:

1. :class:`PermissionManager` — grades every tool by risk and decides whether
   it runs, needs confirmation, or is refused outright.
2. :class:`PathSandbox` — confines all filesystem tools to an allow-listed set
   of roots and blocks sensitive patterns (SSH keys, credentials, ``.env``).
3. :class:`CommandPolicy` — screens shell commands against a hard denylist of
   destructive patterns before anything is executed.

Nothing here trusts the model: a plan produced by any LLM is still filtered by
all three guards.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger

logger = get_logger("security")


class PermissionLevel(str, Enum):
    """Execution permission levels for tools and actions, ordered by risk."""

    READ = "READ"
    LOW_RISK_ACTION = "LOW_RISK_ACTION"
    DESKTOP_ACTION = "DESKTOP_ACTION"
    NETWORK_ACTION = "NETWORK_ACTION"
    WRITE_ACTION = "WRITE_ACTION"
    CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
    HIGH_RISK_ACTION = "HIGH_RISK_ACTION"
    BLOCKED = "BLOCKED"


#: Ascending risk order, used for comparisons and UI badges.
RISK_ORDER: tuple[PermissionLevel, ...] = (
    PermissionLevel.READ,
    PermissionLevel.LOW_RISK_ACTION,
    PermissionLevel.DESKTOP_ACTION,
    PermissionLevel.NETWORK_ACTION,
    PermissionLevel.WRITE_ACTION,
    PermissionLevel.CONFIRM_REQUIRED,
    PermissionLevel.HIGH_RISK_ACTION,
    PermissionLevel.BLOCKED,
)


def risk_rank(level: PermissionLevel) -> int:
    """Numeric rank of a permission level (higher means riskier)."""
    try:
        return RISK_ORDER.index(level)
    except ValueError:
        return len(RISK_ORDER)


class PermissionDecision(str, Enum):
    """Authorization evaluation result."""

    ALLOWED = "ALLOWED"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    DENIED = "DENIED"


@dataclass
class PermissionVerdict:
    """A decision plus the human-readable reason behind it."""

    decision: PermissionDecision
    reason: str = ""
    level: PermissionLevel = PermissionLevel.READ

    @property
    def allowed(self) -> bool:
        return self.decision == PermissionDecision.ALLOWED

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "permission_level": self.level.value,
        }


class PermissionManager:
    """Evaluates whether a tool execution is permitted."""

    def __init__(
        self,
        auto_approve_low_risk: bool | None = None,
        auto_approve_desktop: bool | None = None,
        allow_high_risk: bool | None = None,
    ):
        self.auto_approve_low_risk = (
            settings.AUTO_APPROVE_LOW_RISK if auto_approve_low_risk is None else auto_approve_low_risk
        )
        self.auto_approve_desktop = (
            settings.AUTO_APPROVE_DESKTOP_ACTIONS if auto_approve_desktop is None else auto_approve_desktop
        )
        self.allow_high_risk = (
            settings.ALLOW_HIGH_RISK_ACTIONS if allow_high_risk is None else allow_high_risk
        )
        #: Tool names the user has explicitly blocked at runtime.
        self.blocked_tools: set[str] = set()
        #: Tool names the user has granted a standing approval for.
        self.always_allow: set[str] = set()

    # ------------------------------------------------------------------ API
    def evaluate_detailed(
        self,
        tool_name: str,
        permission_level: PermissionLevel,
        user_approved: bool = False,
    ) -> PermissionVerdict:
        """Full authorization evaluation with an explanation."""
        if tool_name in self.blocked_tools:
            logger.warning("Tool '%s' is blocked by user policy.", tool_name)
            return PermissionVerdict(
                PermissionDecision.DENIED,
                f"'{tool_name}' is blocked by user policy.",
                permission_level,
            )

        if permission_level == PermissionLevel.BLOCKED:
            logger.warning("Blocked tool execution attempt: %s", tool_name)
            return PermissionVerdict(
                PermissionDecision.DENIED,
                f"'{tool_name}' is permanently blocked.",
                permission_level,
            )

        approved = user_approved or tool_name in self.always_allow

        if permission_level == PermissionLevel.READ:
            return PermissionVerdict(PermissionDecision.ALLOWED, "Read-only action.", permission_level)

        if permission_level == PermissionLevel.LOW_RISK_ACTION:
            if self.auto_approve_low_risk or approved:
                return PermissionVerdict(PermissionDecision.ALLOWED, "Low-risk action.", permission_level)
            return PermissionVerdict(
                PermissionDecision.REQUIRES_CONFIRMATION,
                "Low-risk auto-approval is disabled.",
                permission_level,
            )

        if permission_level in (PermissionLevel.DESKTOP_ACTION, PermissionLevel.NETWORK_ACTION):
            if self.auto_approve_desktop or approved:
                return PermissionVerdict(
                    PermissionDecision.ALLOWED,
                    "Desktop/network automation is auto-approved.",
                    permission_level,
                )
            return PermissionVerdict(
                PermissionDecision.REQUIRES_CONFIRMATION,
                "Desktop automation requires confirmation.",
                permission_level,
            )

        if permission_level == PermissionLevel.WRITE_ACTION:
            if approved or self.auto_approve_desktop:
                return PermissionVerdict(PermissionDecision.ALLOWED, "Sandboxed write action.", permission_level)
            return PermissionVerdict(
                PermissionDecision.REQUIRES_CONFIRMATION,
                "Writing files requires confirmation.",
                permission_level,
            )

        if permission_level == PermissionLevel.CONFIRM_REQUIRED:
            if approved:
                return PermissionVerdict(PermissionDecision.ALLOWED, "User confirmed.", permission_level)
            return PermissionVerdict(
                PermissionDecision.REQUIRES_CONFIRMATION,
                "This action needs your explicit confirmation.",
                permission_level,
            )

        if permission_level == PermissionLevel.HIGH_RISK_ACTION:
            if not self.allow_high_risk:
                return PermissionVerdict(
                    PermissionDecision.DENIED,
                    "High-risk actions are disabled (set ALLOW_HIGH_RISK_ACTIONS=true to enable).",
                    permission_level,
                )
            if approved:
                return PermissionVerdict(PermissionDecision.ALLOWED, "High-risk action confirmed.", permission_level)
            return PermissionVerdict(
                PermissionDecision.REQUIRES_CONFIRMATION,
                "High-risk action needs explicit confirmation.",
                permission_level,
            )

        return PermissionVerdict(PermissionDecision.DENIED, "Unknown permission level.", permission_level)

    def evaluate(
        self,
        tool_name: str,
        permission_level: PermissionLevel,
        user_approved: bool = False,
    ) -> PermissionDecision:
        """Backwards-compatible decision-only evaluation."""
        return self.evaluate_detailed(tool_name, permission_level, user_approved).decision

    # --------------------------------------------------------- user controls
    def block_tool(self, tool_name: str) -> None:
        self.blocked_tools.add(tool_name)
        self.always_allow.discard(tool_name)

    def unblock_tool(self, tool_name: str) -> None:
        self.blocked_tools.discard(tool_name)

    def grant_always(self, tool_name: str) -> None:
        self.always_allow.add(tool_name)
        self.blocked_tools.discard(tool_name)

    def revoke_always(self, tool_name: str) -> None:
        self.always_allow.discard(tool_name)

    def snapshot(self) -> dict[str, Any]:
        return {
            "auto_approve_low_risk": self.auto_approve_low_risk,
            "auto_approve_desktop": self.auto_approve_desktop,
            "allow_high_risk": self.allow_high_risk,
            "blocked_tools": sorted(self.blocked_tools),
            "always_allow": sorted(self.always_allow),
        }


# =============================================================================
# Filesystem sandbox
# =============================================================================


class SandboxError(PermissionError):
    """Raised when a path escapes the sandbox or matches a denied pattern."""


@dataclass
class PathSandbox:
    """Confines filesystem access to an allow-listed set of roots."""

    allowed_roots: list[Path] = field(default_factory=list)
    denied_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls) -> "PathSandbox":
        """Build the sandbox described by the current configuration."""
        roots: list[Path] = []
        for raw in settings.FS_ALLOWED_ROOTS:
            try:
                roots.append(Path(os.path.expandvars(raw)).expanduser().resolve())
            except OSError:
                continue

        if not roots:
            home = paths.home_dir()
            candidates = [
                paths.workspace_dir(),
                paths.data_dir(),
                home / "Desktop",
                home / "Documents",
                home / "Downloads",
                home / "Pictures",
                home / "Music",
                home / "Videos",
            ]
            for candidate in candidates:
                try:
                    roots.append(candidate.expanduser().resolve())
                except OSError:
                    continue

        return cls(allowed_roots=roots, denied_patterns=list(settings.FS_DENIED_PATTERNS))

    # ------------------------------------------------------------------ API
    def is_denied_pattern(self, path: Path) -> bool:
        """True when the path matches any denied glob pattern."""
        text = path.as_posix()
        lowered = text.lower()
        for pattern in self.denied_patterns:
            if fnmatch.fnmatch(text, pattern) or fnmatch.fnmatch(lowered, pattern.lower()):
                return True
        return False

    def is_within_roots(self, path: Path) -> bool:
        """True when the resolved path lives under an allowed root."""
        for root in self.allowed_roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def resolve(self, raw_path: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
        """Resolve and validate a user- or model-supplied path.

        Symlinks are resolved *before* validation, so a symlink pointing out of
        the sandbox is rejected rather than followed.
        """
        candidate = Path(os.path.expandvars(str(raw_path))).expanduser()
        if not candidate.is_absolute():
            candidate = paths.workspace_dir() / candidate

        resolved = candidate.resolve()

        if self.is_denied_pattern(resolved):
            raise SandboxError(f"Path '{resolved}' matches a protected pattern and cannot be accessed.")

        if not self.is_within_roots(resolved):
            allowed = ", ".join(str(r) for r in self.allowed_roots) or "(none)"
            raise SandboxError(
                f"Path '{resolved}' is outside the allowed workspace. Allowed roots: {allowed}"
            )

        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Path '{resolved}' does not exist.")

        return resolved

    def describe(self) -> dict[str, Any]:
        return {
            "allowed_roots": [str(r) for r in self.allowed_roots],
            "denied_patterns": list(self.denied_patterns),
            "max_read_bytes": settings.FS_MAX_READ_BYTES,
            "max_write_bytes": settings.FS_MAX_WRITE_BYTES,
        }


# =============================================================================
# Shell command policy
# =============================================================================

#: Patterns that are refused no matter what the user approves. These are
#: whole-system destroyers with no legitimate assistant use case.
HARD_DENY_PATTERNS: tuple[str, ...] = (
    r"rm\s+(-[a-zA-Z]*\s+)*(/|/\*|~|~/)\s*$",
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f",
    r":\(\)\s*\{.*\}\s*;\s*:",              # fork bomb
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\b[^|]*\bof=/dev/(sd|nvme|hd|disk)",
    r">\s*/dev/(sd|nvme|hd|disk)",
    r"\bformat\s+[a-zA-Z]:",
    r"\bdiskpart\b",
    r"\bcipher\s+/w",
    r"Remove-Item\s+.*-Recurse.*(C:\\\\?|\\\\)\s*$",
    r"\bvssadmin\b.*delete\s+shadows",
    r"\bbcdedit\b",
    r"\bshutdown\s+/r\s+/o",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bchown\s+-R\s+.*\s+/\s*$",
    r"\bhistory\s+-c\b",
    r"\bcurl\b[^|]*\|\s*(ba)?sh",
    r"\bwget\b[^|]*\|\s*(ba)?sh",
    r"\biwr\b.*\|\s*iex",
    r"Invoke-Expression",
    r"\bnc\b\s+-l.*-e\s*/bin/(ba)?sh",
    r"\bcrontab\s+-r\b",
    r"\bkillall5\b",
    r"\binit\s+0\b",
)

#: Commands that are safe enough to run without confirmation.
SAFE_COMMAND_ALLOWLIST: frozenset[str] = frozenset(
    {
        "echo", "pwd", "ls", "dir", "cat", "type", "head", "tail", "wc", "date",
        "whoami", "hostname", "uname", "df", "du", "free", "uptime", "which",
        "where", "python", "python3", "pip", "git", "node", "npm", "tree",
        "systeminfo", "ipconfig", "ifconfig", "ping", "curl", "nslookup",
        "tasklist", "ps", "top", "env", "printenv", "grep", "findstr", "sort",
    }
)


@dataclass
class CommandVerdict:
    """Result of screening a shell command."""

    allowed: bool
    requires_confirmation: bool
    reason: str
    program: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "reason": self.reason,
            "program": self.program,
        }


class CommandPolicy:
    """Screens shell commands before execution."""

    def __init__(self, extra_deny: Iterable[str] = (), allowlist: Optional[Iterable[str]] = None):
        self._deny = [re.compile(p, re.IGNORECASE) for p in HARD_DENY_PATTERNS]
        self._deny.extend(re.compile(p, re.IGNORECASE) for p in extra_deny)
        self._allowlist = frozenset(allowlist) if allowlist is not None else SAFE_COMMAND_ALLOWLIST

    @staticmethod
    def program_of(command: str) -> str:
        """Best-effort extraction of the invoked program name."""
        text = command.strip()
        if not text:
            return ""
        try:
            parts = shlex.split(text, posix=not os.name == "nt")
        except ValueError:
            parts = text.split()
        if not parts:
            return ""
        program = Path(parts[0]).name.lower()
        return program[:-4] if program.endswith(".exe") else program

    def screen(self, command: str) -> CommandVerdict:
        """Screen a command and return the verdict."""
        text = (command or "").strip()
        if not text:
            return CommandVerdict(False, False, "Empty command.")

        if not settings.ALLOW_SHELL_TOOL:
            return CommandVerdict(
                False, False,
                "The shell tool is disabled. Set ALLOW_SHELL_TOOL=true to enable it.",
                self.program_of(text),
            )

        for pattern in self._deny:
            if pattern.search(text):
                logger.warning("Refused destructive command matching %r", pattern.pattern)
                return CommandVerdict(
                    False, False,
                    "Refused: the command matches a destructive pattern that IRIS never runs.",
                    self.program_of(text),
                )

        program = self.program_of(text)
        chained = any(token in text for token in ("&&", "||", ";", "|", "`", "$(" ))

        if program in self._allowlist and not chained:
            return CommandVerdict(True, False, "Allow-listed read-only command.", program)

        return CommandVerdict(
            True, True,
            "Command is permitted but needs your confirmation before it runs.",
            program,
        )


default_permission_manager = PermissionManager()
default_path_sandbox = PathSandbox.from_settings()
default_command_policy = CommandPolicy()


def refresh_security() -> None:
    """Rebuild security singletons after a settings change."""
    global default_permission_manager, default_path_sandbox, default_command_policy
    default_permission_manager = PermissionManager()
    default_path_sandbox = PathSandbox.from_settings()
    default_command_policy = CommandPolicy()
