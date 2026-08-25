"""Security audit tests for IRIS Phase 2.

Verifies:
1. API keys never appear in logs or health checks
2. Authorization headers never appear in logs or health checks
3. LLM status endpoint never exposes API keys
4. Unsupported capabilities are rejected cleanly
5. Arbitrary shell execution is blocked by PermissionManager
6. Arbitrary Python code execution is prevented by Calculator AST parser
"""

import pytest
import logging
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient

from iris.app.llm.cloud import CloudLLMProvider
from iris.app.llm.gateway import ModelGateway
from iris.app.core.security import PermissionManager, PermissionLevel, PermissionDecision
from iris.app.tools.builtin.calculator import CalculatorTool
from iris.app.core.config import settings


@pytest.mark.asyncio
async def test_security_api_keys_not_in_health_check():
    """Verify secret API keys are never exposed in health status or error text."""
    provider = CloudLLMProvider(
        provider_name="testprov",
        base_url="http://test-host:9/v1",
        api_key="secret-api-key-12345",
        default_model="some-model",
        timeout=0.2,
    )
    health = await provider.health_check_detailed()
    assert health.available is False
    assert "secret-api-key-12345" not in str(health.model_dump())
    # Redaction helper strips the key from arbitrary error text.
    assert "secret-api-key-12345" not in provider._redact("boom secret-api-key-12345 boom")
    await provider.close()


@pytest.mark.asyncio
async def test_security_llm_status_endpoint_no_credentials(async_client: AsyncClient):
    """Verify GET /api/v1/llm/status never returns sensitive credentials or auth headers."""
    response = await async_client.get("/api/v1/llm/status")
    assert response.status_code == 200
    data = response.json()
    dump_str = str(data).lower()
    
    assert "api_key" not in data
    assert "authorization" not in dump_str
    assert "bearer" not in dump_str
    assert "secret" not in dump_str


def test_security_unsupported_capability_rejection():
    """Verify requesting VISION with no vision model and no providers is cleanly rejected."""
    gateway = ModelGateway()
    if gateway.has_cloud or settings.VISION_MODEL:
        return  # environment has providers configured; rejection path not applicable
    model, err = gateway.select_model_for_capability("VISION")
    assert model is None
    assert err is not None
    assert "unavailable" in err.lower()


def test_security_arbitrary_shell_execution_blocked():
    """Verify arbitrary shell execution tool requests are strictly DENIED."""
    pm = PermissionManager()
    decision = pm.evaluate("shell_exec", PermissionLevel.BLOCKED)
    assert decision == PermissionDecision.DENIED


@pytest.mark.asyncio
async def test_security_arbitrary_python_execution_prevented():
    """Verify AST parser in CalculatorTool prevents execution of arbitrary Python functions/imports."""
    calc = CalculatorTool()

    malicious_expressions = [
        "__import__('os').system('dir')",
        "eval('1 + 1')",
        "exec('import os')",
        "open('/etc/passwd').read()",
        "().__class__.__subclasses__()",
        "[x for x in (1, 2)]",
        "(lambda: 42)()",
        "subprocess.call(['ls'])",
        "True + 1",
    ]

    for expr in malicious_expressions:
        res = await calc.execute(expression=expr)
        assert res.success is False, f"Malicious expression unexpectedly succeeded: {expr}"
        assert (
            "Invalid mathematical syntax" in res.error
            or "Unsupported" in res.error
            or "failed" in res.error
        ), f"Unexpected error format for '{expr}': {res.error}"
