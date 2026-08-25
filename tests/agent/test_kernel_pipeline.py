"""Tests for the layered kernel pipeline: smalltalk, NLU dispatch, confirmations."""

import pytest

from iris.app.agent.kernel import AgentKernel
from iris.app.core.security import PermissionLevel, PermissionManager
from iris.app.schemas.tasks import TaskStatus
from iris.app.schemas.tools import ToolCategory, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError
from iris.app.tools.registry import ToolRegistry


class EchoTool(BaseTool):
    name = "echo_demo"
    description = "Echo for tests."
    permission_level = PermissionLevel.READ
    category = ToolCategory.CORE
    input_schema = ToolParameterSchema(properties={"text": {"type": "string"}})

    async def _run(self, text: str = "hi", **_):
        return {"echo": text, "speech": f"You said {text}."}


class RiskyTool(BaseTool):
    name = "risky_demo"
    description = "Needs confirmation."
    permission_level = PermissionLevel.CONFIRM_REQUIRED
    category = ToolCategory.CORE

    async def _run(self, **_):
        return {"speech": "Risky thing done."}


class FailingTool(BaseTool):
    name = "failing_demo"
    description = "Always fails."
    permission_level = PermissionLevel.READ

    async def _run(self, **_):
        raise ToolError("It broke.", speech="Broke.")


from iris.app.nlu.engine import IntentEngine
from iris.app.nlu.rules import Rule, _rx


def make_kernel() -> AgentKernel:
    registry = ToolRegistry()
    registry.register(EchoTool(), quiet=True)
    registry.register(RiskyTool(), quiet=True)
    registry.register(FailingTool(), quiet=True)
    rules = [
        Rule(name="echo", intent="test", tool="echo_demo",
             pattern=_rx(r"^echo\s+(?P<text>.+)$")),
        Rule(name="risky", intent="test", tool="risky_demo", pattern=_rx(r"^do the risky thing$")),
        Rule(name="fail", intent="test", tool="failing_demo", pattern=_rx(r"^fail(?: now)?$")),
    ]
    return AgentKernel(
        tool_registry=registry,
        permission_manager=PermissionManager(),
        intent_engine=IntentEngine(rules),
    )


async def test_smalltalk_layer():
    kernel = make_kernel()
    res = await kernel.process_request("hello")
    assert res.handler == "smalltalk"
    assert res.status == "COMPLETED"


async def test_nlu_dispatch_with_speech():
    kernel = make_kernel()
    res = await kernel.process_request("echo good morning")
    assert res.handler == "nlu"
    assert res.intent_detected == "echo_demo"
    assert res.speech == "You said good morning."
    assert res.tools_executed[0].success is True


async def test_wake_word_stripped_before_matching():
    kernel = make_kernel()
    res = await kernel.process_request("hey iris echo hi there")
    assert res.handler == "nlu"
    assert res.tools_executed[0].arguments["text"] == "hi there"


async def test_confirmation_roundtrip_approved():
    kernel = make_kernel()
    res = await kernel.process_request("do the risky thing")
    assert res.status == TaskStatus.WAITING_FOR_CONFIRMATION.value
    assert res.pending_action is not None
    final = await kernel.resume_task_confirmation(res.task_id, approved=True)
    assert final.status == "COMPLETED"
    assert "Risky thing done." in (final.speech or final.response)


async def test_confirmation_roundtrip_rejected():
    kernel = make_kernel()
    res = await kernel.process_request("do the risky thing")
    final = await kernel.resume_task_confirmation(res.task_id, approved=False)
    assert final.status == "COMPLETED"
    assert "won't run" in final.response


async def test_confirmation_unknown_task():
    kernel = make_kernel()
    with pytest.raises(ValueError):
        await kernel.resume_task_confirmation("task_nope", approved=True)


async def test_tool_failure_reports_cleanly():
    kernel = make_kernel()
    res = await kernel.process_request("fail now")
    assert res.status == "FAILED"
    assert res.error == "It broke."
    assert res.speech == "Broke."


async def test_unmatched_falls_to_agent_loop():
    kernel = make_kernel()
    res = await kernel.process_request("please compose a haiku about rivers")
    # No cloud providers in tests: the offline reasoner answers.
    assert res.handler == "agent"
    assert res.status == "COMPLETED"
    assert res.response
