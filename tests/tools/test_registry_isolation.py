"""Registering a tool must not overwrite state the tool already owns.

``ToolRegistry.register`` hands each tool a back-reference to the catalogue it
now lives in, so a tool that reports on the catalogue reports on the right one.
That reference was called ``registry`` — the same attribute the ESP32 tools use
for their ``DeviceRegistry`` of boards. Registering them replaced it, and every
device tool then called DeviceRegistry methods on a ToolRegistry.

Nothing failed at registration. It failed later, inside the tool, as
``'ToolRegistry' object has no attribute 'add'`` — which is how "add device
face at 192.168.1.70 as face" came back as "That one didn't go through."
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from iris.app.tools.registry import ToolRegistry
from iris.app.tools.loader import load_all_tools
from iris.app.tools.devices.registry import DeviceRegistry
from iris.app.tools.devices.esp32 import RegisterDeviceTool, ListDevicesTool


@pytest.fixture
def devices(tmp_path) -> DeviceRegistry:
    return DeviceRegistry(path=tmp_path / "devices.json")


def test_registering_keeps_the_tools_own_registry(devices):
    tool = RegisterDeviceTool(devices)
    ToolRegistry().register(tool, quiet=True)
    assert tool.registry is devices, "the catalogue overwrote the device registry"


def test_the_catalogue_is_still_reachable(devices):
    """The back-reference the rename was protecting still has to work."""
    tool = RegisterDeviceTool(devices)
    catalogue = ToolRegistry()
    catalogue.register(tool, quiet=True)
    assert tool.tool_registry is catalogue


@pytest.mark.asyncio
async def test_register_device_works_after_being_registered(devices):
    """The end-to-end shape of the report: add a board, then list it."""
    catalogue = ToolRegistry()
    add = RegisterDeviceTool(devices)
    listing = ListDevicesTool(devices)
    catalogue.register(add, quiet=True)
    catalogue.register(listing, quiet=True)

    res = await add.execute(name="face", address="192.168.1.70", kind="face")
    assert res.success, res.error
    assert "192.168.1.70" in res.result["speech"]

    shown = await listing.execute()
    assert shown.success, shown.error
    assert any(d["name"] == "face" for d in shown.result["devices"])


def test_no_loaded_tool_has_its_registry_clobbered():
    """A sweep, so the next attribute collision is caught at the source.

    Any tool that arrives holding its own ``registry`` must still hold it
    after the catalogue has had it.
    """
    from iris.app.tools.devices import esp32

    before = {t.name: getattr(t, "registry", None) for t in esp32.get_tools()}
    catalogue = ToolRegistry()
    tools = esp32.get_tools()
    for tool in tools:
        catalogue.register(tool, quiet=True)
    for tool in tools:
        kept = getattr(tool, "registry", None)
        assert kept is not catalogue, f"{tool.name}'s registry was replaced by the catalogue"
        assert type(kept) is type(before[tool.name])


def test_the_whole_catalogue_still_loads():
    """The rename touches base.py, so prove nothing stopped registering."""
    catalogue = ToolRegistry()
    count = load_all_tools(catalogue, quiet=True)
    assert count >= 89
    instances = [catalogue.get(meta.name) for meta in catalogue.list_tools()]
    assert instances and all(t is not None for t in instances)
    assert all(t.tool_registry is catalogue for t in instances)


class TestAFailureKeepsItsReason:
    """An unexpected crash must not be reported as nothing at all.

    The generic handler set ``speech="That didn't work."``. That outranks
    ``error`` in ``spoken_or_display()``, and the persona then recognised it as
    a bare failure and substituted another one — so the reason was discarded
    twice and the user was told "That one didn't go through." about an
    AttributeError that named the broken call.
    """

    @staticmethod
    def _boom_tool():
        from iris.app.core.security import PermissionLevel
        from iris.app.schemas.tools import ToolCategory, ToolParameterSchema
        from iris.app.tools.base import BaseTool

        class Boom(BaseTool):
            name = "boom"
            description = "raises"
            category = ToolCategory.SYSTEM
            permission_level = PermissionLevel.READ
            input_schema = ToolParameterSchema()

            async def _run(self):
                raise AttributeError("'ToolRegistry' object has no attribute 'add'")

        return Boom()

    @pytest.mark.asyncio
    async def test_the_reason_survives_to_the_summary(self):
        res = await self._boom_tool().execute()
        assert not res.success
        assert "no attribute 'add'" in res.error
        assert "no attribute 'add'" in res.spoken_or_display()

    @pytest.mark.asyncio
    async def test_the_reason_survives_the_persona(self):
        from iris.app.agent.persona import default_voice

        res = await self._boom_tool().execute()
        spoken = default_voice.acknowledge(res.spoken_or_display(), success=False)
        assert "no attribute 'add'" in spoken, spoken

    @pytest.mark.asyncio
    async def test_an_exception_with_no_message_still_names_itself(self):
        from iris.app.core.security import PermissionLevel
        from iris.app.schemas.tools import ToolCategory, ToolParameterSchema
        from iris.app.tools.base import BaseTool

        class Silent(BaseTool):
            name = "silent"
            description = "raises nothing useful"
            category = ToolCategory.SYSTEM
            permission_level = PermissionLevel.READ
            input_schema = ToolParameterSchema()

            async def _run(self):
                raise RuntimeError()

        res = await Silent().execute()
        assert not res.success
        assert res.error == "RuntimeError"
