"""Reading sensors over both transports, and the flame sensor's own path.

A linked node pushes readings continuously, so a sensor question should be
answered from what is already on the server rather than by a round trip to a
board on the other side of the internet. That shortcut has to be correct about
one thing above all: it must never serve a stale reading as if it were current.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from iris.app.nlu.engine import IntentEngine
from iris.app.nodes.link import NodeLink, NodeLinkHub
from iris.app.tools.devices import esp32 as esp32_mod
from iris.app.tools.devices.esp32 import TELEMETRY_MAX_AGE_S, DeviceSensorsTool
from iris.app.tools.devices.registry import Device, DeviceRegistry


@pytest.fixture()
def registry(tmp_path):
    return DeviceRegistry(path=tmp_path / "devices.json")


@pytest.fixture()
def hub():
    return NodeLinkHub()


@pytest.fixture()
def linked(registry, hub, monkeypatch):
    """A face node dialled in, with the tool pointed at this hub."""
    monkeypatch.setattr(esp32_mod, "default_node_hub", hub)
    registry.add(Device(name="face", kind="face", transport="link"))

    async def send(_frame: str) -> None:
        return None

    link = NodeLink(name="face", kind="face", send=send)
    hub.register(link)
    return link


class TestSummary:
    """Danger has to lead the sentence, not be buried after the light level."""

    def test_flame_comes_first(self):
        summary = DeviceSensorsTool._summarize(
            {"flame": True, "light_percent": 40, "motion_recent": False,
             "gas_raw": 500, "gas_alarm": False},
            "all",
        )
        assert summary.startswith("FIRE DETECTED")

    def test_gas_alarm_outranks_the_ordinary_readings(self):
        summary = DeviceSensorsTool._summarize(
            {"flame": False, "gas_raw": 3000, "gas_alarm": True, "light_percent": 40},
            "all",
        )
        assert summary.index("GAS ALARM") < summary.index("light")

    def test_no_flame_is_reported_plainly(self):
        assert "no flame" in DeviceSensorsTool._summarize({"flame": False}, "all")

    def test_asking_for_one_sensor_gets_one_answer(self):
        data = {"flame": True, "gas_raw": 500, "gas_alarm": False,
                "light_percent": 40, "motion_recent": True, "motion": True,
                "distance_cm": 30}
        assert DeviceSensorsTool._summarize(data, "flame") == "FIRE DETECTED."
        assert "light" not in DeviceSensorsTool._summarize(data, "flame")

    def test_a_node_with_nothing_wired_says_so(self):
        assert "no matching sensors" in DeviceSensorsTool._summarize({}, "all")

    @pytest.mark.parametrize("sensor", ["all", "motion", "gas", "light", "distance", "flame"])
    def test_every_advertised_sensor_is_handled(self, sensor):
        data = {"flame": False, "gas_raw": 500, "gas_alarm": False,
                "light_percent": 40, "light_raw": 1600,
                "motion": False, "motion_recent": False, "distance_cm": 55}
        summary = DeviceSensorsTool._summarize(data, sensor)
        assert summary and "no matching sensors" not in summary

    def test_flame_is_in_the_tool_schema(self):
        enum = DeviceSensorsTool().input_schema.properties["sensor"]["enum"]
        assert "flame" in enum


class TestTelemetryBackedReads:
    @pytest.mark.asyncio
    async def test_a_pushed_reading_answers_without_a_round_trip(self, registry, linked):
        linked.telemetry = {"flame": True, "gas_raw": 700, "gas_alarm": False}
        linked.telemetry_at = time.monotonic()

        res = await DeviceSensorsTool(registry).execute(sensor="flame")
        assert res.success, res.error
        assert res.result["source"] == "telemetry"
        assert "FIRE DETECTED" in res.speech

    @pytest.mark.asyncio
    async def test_a_stale_reading_is_not_served_as_current(self, registry, linked, monkeypatch):
        """The whole point of a sensor question is that the answer is now."""
        linked.telemetry = {"flame": True}
        linked.telemetry_at = time.monotonic() - TELEMETRY_MAX_AGE_S - 1

        asked = []

        async def fake_request(device, path, params=None, hub=None):
            asked.append(path)
            return {"flame": False}

        monkeypatch.setattr(esp32_mod, "device_request", fake_request)
        res = await DeviceSensorsTool(registry).execute(sensor="flame")
        assert asked == ["/sensors"], "a stale cache was used instead of asking"
        assert res.result["source"] == "live"

    @pytest.mark.asyncio
    async def test_an_empty_cache_falls_back_to_asking(self, registry, linked, monkeypatch):
        asked = []

        async def fake_request(device, path, params=None, hub=None):
            asked.append(path)
            return {"motion": True, "motion_recent": True}

        monkeypatch.setattr(esp32_mod, "device_request", fake_request)
        res = await DeviceSensorsTool(registry).execute(sensor="motion")
        assert asked == ["/sensors"]
        assert res.result["source"] == "live"

    @pytest.mark.asyncio
    async def test_a_lan_device_always_asks(self, registry, hub, monkeypatch):
        """There is no pushed reading to use, so the cache must not be consulted."""
        monkeypatch.setattr(esp32_mod, "default_node_hub", hub)
        registry.add(Device(name="room", base_url="http://192.168.1.70", kind="sensor"))
        asked = []

        async def fake_request(device, path, params=None, hub=None):
            asked.append(path)
            return {"gas_raw": 900, "gas_alarm": False}

        monkeypatch.setattr(esp32_mod, "device_request", fake_request)
        res = await DeviceSensorsTool(registry).execute(sensor="gas")
        assert asked == ["/sensors"]
        assert res.result["source"] == "live"

    @pytest.mark.asyncio
    async def test_a_face_node_answers_sensor_questions_too(self, registry, linked):
        """One board does both jobs, so one registration should cover both."""
        linked.telemetry = {"motion": True, "motion_recent": True}
        linked.telemetry_at = time.monotonic()
        res = await DeviceSensorsTool(registry).execute(sensor="motion")
        assert res.success
        assert "Motion detected" in res.speech

    @pytest.mark.asyncio
    async def test_no_node_at_all_explains_what_to_do(self, registry, hub, monkeypatch):
        monkeypatch.setattr(esp32_mod, "default_node_hub", hub)
        res = await DeviceSensorsTool(registry).execute(sensor="all")
        assert not res.success
        assert "add device" in (res.error or "")


class TestFlameIntents:
    @pytest.fixture()
    def engine(self):
        return IntentEngine()

    @pytest.mark.parametrize("text", [
        "is there a fire", "is there any fire", "any fire", "is there any flame",
        "fire check", "flame check", "fire detected", "flame status",
        "aag lagi hai kya", "aag hai", "fire check karo",
    ])
    def test_fire_questions_route_to_the_flame_sensor(self, engine, text):
        match = engine.match(text)
        assert match is not None, f"{text!r} matched nothing"
        assert match.tool_name == "device_sensors"
        assert match.arguments["sensor"] == "flame"

    @pytest.mark.parametrize("text,sensor", [
        ("is there any motion", "motion"),
        ("gas level", "gas"),
        ("how far is the object", "distance"),
        ("check the sensors", "all"),
    ])
    def test_the_other_sensor_questions_still_work(self, engine, text, sensor):
        match = engine.match(text)
        assert match.tool_name == "device_sensors"
        assert match.arguments["sensor"] == sensor
