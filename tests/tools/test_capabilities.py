"""She reports what she has, rather than describing what she imagines.

Asked to show everything she could do, Iris wrote a demo instead of running
one: a timer never started, a reminder never set, and a twelve-core Linux box
reported to someone sitting at a four-core MacBook Air. Part of that was a
missing capability — there was no tool that answers "what can you do", so the
only material available for the question was invention.
"""

from __future__ import annotations

import pytest

from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolParameterSchema
from iris.app.tools.base import BaseTool
from iris.app.tools.builtin.capabilities import CapabilitiesTool
from iris.app.tools.registry import ToolRegistry


class _Fake(BaseTool):
    category = ToolCategory.FILES
    permission_level = PermissionLevel.READ
    input_schema = ToolParameterSchema(properties={})

    def __init__(self, name, available=True, category=None):
        self.name = name
        self.description = f"Does {name}."
        if category:
            self.category = category
        self._available = available
        super().__init__()

    def supports_current_os(self):
        return self._available

    async def _run(self, **_):
        return {"speech": "ok"}


@pytest.fixture()
def registry():
    r = ToolRegistry()
    r.register(CapabilitiesTool(), quiet=True)
    r.register(_Fake("read_file"), quiet=True)
    r.register(_Fake("write_file"), quiet=True)
    r.register(_Fake("open_app", category=ToolCategory.DESKTOP), quiet=True)
    r.register(_Fake("windows_only", available=False, category=ToolCategory.DESKTOP), quiet=True)
    return r


class TestItReportsTheRealRegistry:
    async def test_it_counts_what_is_actually_there(self, registry):
        result = await registry.get("capabilities").execute()
        assert result.success
        assert result.result["total"] == 5          # 4 fakes + itself
        assert result.result["available"] == 4

    async def test_a_tool_that_does_not_work_here_is_named_as_such(self, registry):
        result = await registry.get("capabilities").execute()
        assert result.result["unavailable"] == ["windows_only"]
        assert "windows_only" in result.display
        assert "windows_only" not in result.result["groups"].get("desktop", [])

    async def test_it_reads_its_own_registry_not_the_global_one(self, registry):
        """Otherwise it is right in production and silently empty everywhere else."""
        result = await registry.get("capabilities").execute()
        assert set(result.result["groups"]["files"]) == {"read_file", "write_file"}

    async def test_one_group_can_be_asked_for(self, registry):
        result = await registry.get("capabilities").execute(group="files")
        assert set(result.result["groups"]) == {"files"}
        assert result.result["total"] == 2

    async def test_an_unknown_group_says_so_and_names_the_real_ones(self, registry):
        result = await registry.get("capabilities").execute(group="teleportation")
        assert result.success
        assert "teleportation" in result.display
        assert "files" in result.display
        assert result.result["total"] == 0

    async def test_an_empty_registry_is_an_answer_not_a_crash(self):
        bare = ToolRegistry()
        bare.register(CapabilitiesTool(), quiet=True)
        tool = bare.get("capabilities")
        tool.tool_registry = ToolRegistry()          # genuinely empty
        result = await tool.execute()
        assert result.success
        assert result.result["total"] == 0
        assert "can't do anything" in result.speech

    async def test_the_spoken_line_is_short_enough_to_hear(self, registry):
        result = await registry.get("capabilities").execute()
        assert len(result.speech) < 260, result.speech
        assert "5" in result.speech or "4" in result.speech


class TestItIsReachable:
    def test_the_registry_hands_every_tool_a_way_back_to_itself(self, registry):
        for name in ("capabilities", "read_file"):
            assert registry.get(name).tool_registry is registry

    def test_the_phrases_people_use_are_aliases(self):
        aliases = set(CapabilitiesTool().aliases)
        assert {"what can you do", "list tools"} <= aliases
