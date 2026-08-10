"""Tests for builtin tools and tool registry."""

import pytest
from nova.app.tools.builtin.calculator import CalculatorTool
from nova.app.tools.builtin.system_info import SystemInfoTool
from nova.app.tools.builtin.time import TimeTool
from nova.app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_calculator_tool_success():
    tool = CalculatorTool()
    res = await tool.execute(expression="25 * 47")
    assert res.success is True
    assert res.result["result"] == 1175
    assert res.tool_name == "calculator"


@pytest.mark.asyncio
async def test_calculator_tool_invalid_expression():
    tool = CalculatorTool()
    res = await tool.execute(expression="25 + invalid_token")
    assert res.success is False
    assert "Invalid mathematical syntax" in res.error or "Unsupported" in res.error


@pytest.mark.asyncio
async def test_calculator_division_by_zero():
    tool = CalculatorTool()
    res = await tool.execute(expression="100 / 0")
    assert res.success is False
    assert "Division by zero" in res.error


@pytest.mark.asyncio
async def test_calculator_chained_expressions():
    """Regression test for multi-operation arithmetic expressions."""
    tool = CalculatorTool()
    cases = [
        ("78 * 23 * 7", 12558),
        ("20 + 30 + 40", 90),
        ("100 - 20 - 30", 50),
        ("5 * 10 / 2", 25),
        ("(10 + 5) * 2", 30),
        ("10 + 5 * 2", 20),
        ("2 ** 3 ** 2", 512),
        ("15 % 4", 3),
        ("-5 + 10", 5),
    ]
    for expr, expected in cases:
        res = await tool.execute(expression=expr)
        assert res.success is True, f"Failed for expression: {expr}"
        assert res.result["result"] == expected, f"Expected {expected} for '{expr}', got {res.result['result']}"


@pytest.mark.asyncio
async def test_system_info_tool():
    tool = SystemInfoTool()
    res = await tool.execute()
    assert res.success is True
    assert "os" in res.result
    assert "python_version" in res.result
    assert "memory" in res.result


@pytest.mark.asyncio
async def test_time_tool():
    tool = TimeTool()
    res = await tool.execute()
    assert res.success is True
    assert "local_time" in res.result
    assert "timezone" in res.result
    assert "unix_timestamp" in res.result


def test_tool_registry():
    registry = ToolRegistry()
    calc = CalculatorTool()
    registry.register(calc)

    assert registry.is_registered("calculator") is True
    assert registry.get("calculator") == calc
    assert len(registry.list_tools()) == 1
    assert registry.get("non_existent") is None
