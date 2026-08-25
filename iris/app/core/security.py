"""Permission Manager and Security controls for tool execution."""

from enum import Enum
from typing import Dict, Any, Optional
from iris.app.core.config import settings
from iris.app.core.logging import get_logger

logger = get_logger("security")


class PermissionLevel(str, Enum):
    """Execution permission levels for tools and actions."""
    READ = "READ"
    LOW_RISK_ACTION = "LOW_RISK_ACTION"
    CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
    HIGH_RISK_ACTION = "HIGH_RISK_ACTION"
    BLOCKED = "BLOCKED"


class PermissionDecision(str, Enum):
    """Authorization evaluation result."""
    ALLOWED = "ALLOWED"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    DENIED = "DENIED"


class PermissionManager:
    """Evaluates whether a tool execution is permitted."""

    def __init__(self, auto_approve_low_risk: bool = True):
        self.auto_approve_low_risk = auto_approve_low_risk

    def evaluate(
        self,
        tool_name: str,
        permission_level: PermissionLevel,
        user_approved: bool = False,
    ) -> PermissionDecision:
        """Determine authorization status for tool execution."""
        if permission_level == PermissionLevel.BLOCKED:
            logger.warning(f"Blocked tool execution attempt: {tool_name}")
            return PermissionDecision.DENIED

        if permission_level == PermissionLevel.READ:
            return PermissionDecision.ALLOWED

        if permission_level == PermissionLevel.LOW_RISK_ACTION:
            if self.auto_approve_low_risk or user_approved:
                return PermissionDecision.ALLOWED
            return PermissionDecision.REQUIRES_CONFIRMATION

        if permission_level in (PermissionLevel.CONFIRM_REQUIRED, PermissionLevel.HIGH_RISK_ACTION):
            if user_approved:
                return PermissionDecision.ALLOWED
            return PermissionDecision.REQUIRES_CONFIRMATION

        return PermissionDecision.DENIED


default_permission_manager = PermissionManager(
    auto_approve_low_risk=settings.AUTO_APPROVE_LOW_RISK
)
