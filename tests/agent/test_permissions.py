"""Tests for Security Permission Manager."""

import pytest
from iris.app.core.security import PermissionManager, PermissionLevel, PermissionDecision


def test_permission_read():
    pm = PermissionManager()
    decision = pm.evaluate("time", PermissionLevel.READ)
    assert decision == PermissionDecision.ALLOWED


def test_permission_low_risk_auto_approve():
    pm = PermissionManager(auto_approve_low_risk=True)
    decision = pm.evaluate("calculator", PermissionLevel.LOW_RISK_ACTION)
    assert decision == PermissionDecision.ALLOWED


def test_permission_low_risk_no_auto_approve():
    pm = PermissionManager(auto_approve_low_risk=False)
    decision = pm.evaluate("calculator", PermissionLevel.LOW_RISK_ACTION, user_approved=False)
    assert decision == PermissionDecision.REQUIRES_CONFIRMATION

    decision_approved = pm.evaluate("calculator", PermissionLevel.LOW_RISK_ACTION, user_approved=True)
    assert decision_approved == PermissionDecision.ALLOWED


def test_permission_blocked():
    pm = PermissionManager()
    decision = pm.evaluate("shell_exec", PermissionLevel.BLOCKED)
    assert decision == PermissionDecision.DENIED


def test_permission_confirm_required():
    pm = PermissionManager()
    decision_unapproved = pm.evaluate("delete_file", PermissionLevel.CONFIRM_REQUIRED, user_approved=False)
    assert decision_unapproved == PermissionDecision.REQUIRES_CONFIRMATION

    decision_approved = pm.evaluate("delete_file", PermissionLevel.CONFIRM_REQUIRED, user_approved=True)
    assert decision_approved == PermissionDecision.ALLOWED
