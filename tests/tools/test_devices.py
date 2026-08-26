"""Tests for the ESP32 / smart-device control layer."""

from __future__ import annotations

import json

import httpx
import pytest

from iris.app.tools.devices.registry import (
    Device,
    DeviceError,
    DeviceRegistry,
    normalize_base_url,
    normalize_name,
)
from iris.app.tools.devices import esp32 as esp32_mod
from iris.app.tools.devices.esp32 import (
    DeviceCommandTool,
    DeviceMotorTool,
    DeviceStatusTool,
    DeviceSwitchTool,
    ListDevicesTool,
    RegisterDeviceTool,
    RemoveDeviceTool,
)
from iris.app.nlu.engine import IntentEngine


@pytest.fixture()
def registry(tmp_path):
    return DeviceRegistry(path=tmp_path / "devices.json")


@pytest.fixture()
def fake_lan(monkeypatch):
    """Route the device HTTP helper through a fake ESP32."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/status":
            return httpx.Response(200, json={"name": "node", "kind": "relay", "relays": ["off"]})
        if path == "/relay":
            return httpx.Response(200, json={"ch": int(request.url.params["ch"]),
                                             "state": request.url.params["state"]})
        if path == "/motor":
            return httpx.Response(200, json={"motor": request.url.params["dir"]})
        if path == "/servo":
            return httpx.Response(200, json={"servo": int(request.url.params["angle"]),
                                             "hold": "hold" in request.url.params})
        if path == "/led/on":
            return httpx.Response(200, text="OK")
        return httpx.Response(404, json={"error": "unknown endpoint"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(esp32_mod.httpx, "AsyncClient", fake_client)
    return calls


# ---------------------------------------------------------------- registry
class TestRegistry:
    def test_lan_addresses_accepted(self):
        assert normalize_base_url("192.168.1.50") == "http://192.168.1.50"
        assert normalize_base_url("10.0.0.7:8080") == "http://10.0.0.7:8080"
        assert normalize_base_url("http://robot.local/") == "http://robot.local"
        assert normalize_base_url("172.20.1.2") == "http://172.20.1.2"

    def test_public_addresses_rejected(self):
        for bad in ("8.8.8.8", "http://example.com", "https://1.1.1.1"):
            with pytest.raises(DeviceError):
                normalize_base_url(bad)

    def test_name_validation(self):
        assert normalize_name("  Kitchen  Light ") == "kitchen light"
        with pytest.raises(DeviceError):
            normalize_name("!!!")

    def test_persistence_roundtrip(self, tmp_path):
        path = tmp_path / "devices.json"
        r1 = DeviceRegistry(path=path)
        r1.add(Device(name="fan", base_url="http://192.168.1.9", kind="relay"))
        r2 = DeviceRegistry(path=path)
        assert [d.name for d in r2.list()] == ["fan"]

    def test_fuzzy_lookup(self, registry):
        registry.add(Device(name="kitchen light", base_url="http://192.168.1.5", kind="relay"))
        assert registry.get("light").name == "kitchen light"
        assert registry.get("the kitchen light").name == "kitchen light"
        registry.add(Device(name="bedroom light", base_url="http://192.168.1.6", kind="relay"))
        # ambiguous now
        assert registry.get("light") is None


# ------------------------------------------------------------------- tools
class TestDeviceTools:
    @pytest.mark.asyncio
    async def test_register_and_list(self, registry, fake_lan):
        reg = RegisterDeviceTool(registry)
        res = await reg.execute(name="Kitchen Light", address="192.168.1.50", kind="relay")
        assert res.success and res.result["reachable"]
        listing = await ListDevicesTool(registry).execute()
        assert listing.result["count"] == 1

    @pytest.mark.asyncio
    async def test_register_rejects_public_address(self, registry):
        res = await RegisterDeviceTool(registry).execute(name="evil", address="8.8.8.8")
        assert not res.success and "local network" in res.error

    @pytest.mark.asyncio
    async def test_switch_hits_relay_endpoint(self, registry, fake_lan):
        registry.add(Device(name="fan", base_url="http://192.168.1.9", kind="relay", default_channel=2))
        res = await DeviceSwitchTool(registry).execute(device="fan", state="on")
        assert res.success and res.result["state"] == "on"
        assert any("/relay?ch=2&state=on" in url for url in fake_lan)

    @pytest.mark.asyncio
    async def test_switch_uses_custom_command_map(self, registry, fake_lan):
        registry.add(Device(name="bedroom light", base_url="http://192.168.1.8",
                            kind="relay", commands={"on": "/led/on"}))
        res = await DeviceSwitchTool(registry).execute(device="bedroom light", state="on")
        assert res.success
        assert any(url.endswith("/led/on") for url in fake_lan)

    @pytest.mark.asyncio
    async def test_switch_unknown_device_is_helpful(self, registry):
        res = await DeviceSwitchTool(registry).execute(device="garage", state="on")
        assert not res.success and "add device garage" in res.error

    @pytest.mark.asyncio
    async def test_motor_defaults_to_first_motor_device(self, registry, fake_lan):
        registry.add(Device(name="robot", base_url="http://192.168.1.60", kind="motor"))
        res = await DeviceMotorTool(registry).execute(action="forward", speed=999, duration_ms=1500)
        assert res.success
        assert any("dir=forward" in url and "speed=255" in url and "ms=1500" in url for url in fake_lan)

    @pytest.mark.asyncio
    async def test_motor_without_device_explains(self, registry):
        res = await DeviceMotorTool(registry).execute(action="forward")
        assert not res.success and "No motor device" in res.error

    @pytest.mark.asyncio
    async def test_command_path_traversal_blocked(self, registry):
        registry.add(Device(name="node", base_url="http://192.168.1.7"))
        res = await DeviceCommandTool(registry).execute(device="node", command="/../../etc")
        assert not res.success

    @pytest.mark.asyncio
    async def test_status_reports_offline(self, registry, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)
        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(esp32_mod.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
        registry.add(Device(name="fan", base_url="http://192.168.1.9"))
        res = await DeviceStatusTool(registry).execute()
        assert res.success and res.result["devices"][0]["online"] is False

    @pytest.mark.asyncio
    async def test_remove(self, registry):
        registry.add(Device(name="fan", base_url="http://192.168.1.9"))
        res = await RemoveDeviceTool(registry).execute(name="fan")
        assert res.success and registry.list() == []


# --------------------------------------------------------------------- NLU
class TestDeviceNLU:
    engine = IntentEngine()

    @pytest.mark.parametrize("utterance,tool,expected", [
        ("add device kitchen light at 192.168.1.50 as relay", "register_device",
         {"name": "kitchen light", "address": "192.168.1.50", "kind": "relay"}),
        ("turn on the kitchen light", "device_switch", {"device": "kitchen light", "state": "on"}),
        ("switch off the fan", "device_switch", {"device": "fan", "state": "off"}),
        ("fan band karo", "device_switch", {"device": "fan", "state": "off"}),
        ("light chalu kar do", "device_switch", {"device": "light", "state": "on"}),
        ("toggle the socket", "device_switch", {"device": "socket", "state": "toggle"}),
        ("robot forward", "device_motor", {"action": "forward"}),
        ("move the robot left", "device_motor", {"action": "left"}),
        ("robot peeche", "device_motor", {"action": "backward"}),
        ("stop the robot", "device_motor", {"action": "stop"}),
        ("list my devices", "list_devices", {}),
        ("is the light online", "device_status", {"device": "light"}),
    ])
    def test_device_phrases_route(self, utterance, tool, expected):
        match = self.engine.match(utterance)
        assert match is not None, f"no match for {utterance!r}"
        assert match.tool_name == tool
        for key, value in expected.items():
            assert match.arguments.get(key) == value

    @pytest.mark.parametrize("utterance,not_tool", [
        ("turn the volume up", "device_switch"),
        ("turn it up", "device_switch"),
        ("turn on dark mode", "device_switch"),
        ("switch off the screen", "device_switch"),
    ])
    def test_non_device_phrases_do_not_route_to_devices(self, utterance, not_tool):
        match = self.engine.match(utterance)
        assert match is None or match.tool_name != not_tool

    def test_hinglish_open(self):
        match = self.engine.match("notepad kholo")
        assert match and match.tool_name == "open_app"
        match2 = self.engine.match("youtube kholo")
        assert match2 and match2.tool_name == "open_website"


# ---------------------------------------------------------------- sensors
class TestSensorNode:
    @pytest.mark.asyncio
    async def test_sensor_readings_summarized(self, registry, monkeypatch):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/sensors"
            return httpx.Response(200, json={
                "motion": False, "motion_recent": True,
                "gas_raw": 900, "gas_alarm": False,
                "light_raw": 2048, "light_percent": 50,
                "distance_cm": 42, "uptime_s": 10,
            })

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(esp32_mod.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
        registry.add(Device(name="room sensor", base_url="http://192.168.1.70", kind="sensor"))

        res = await DeviceSensorsTool(registry).execute(sensor="all")
        assert res.success
        assert "Motion detected" in res.result["speech"]
        assert "gas level 900 (normal)" in res.result["speech"]
        assert "42 cm" in res.result["speech"]

        res_gas = await DeviceSensorsTool(registry).execute(sensor="gas")
        assert res_gas.result["speech"] == "gas level 900 (normal)."

    @pytest.mark.asyncio
    async def test_gas_alarm_is_loud(self, registry, monkeypatch):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"gas_raw": 3000, "gas_alarm": True})

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(esp32_mod.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
        registry.add(Device(name="kitchen sensor", base_url="http://192.168.1.71", kind="sensor"))
        res = await DeviceSensorsTool(registry).execute(sensor="gas")
        assert "GAS ALARM" in res.result["speech"]

    @pytest.mark.asyncio
    async def test_no_sensor_node_explains(self, registry):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        res = await DeviceSensorsTool(registry).execute()
        assert not res.success and "No sensor node" in res.error

    @pytest.mark.asyncio
    async def test_climate_and_two_ultrasonics(self, registry, monkeypatch):
        """A node with the DHT and both HC-SR04s fitted.

        The two distances have to read as one phrase — "82 cm ahead, 15 cm
        behind" — because two bare numbers leave the listener to guess which
        end of the robot each belongs to.
        """
        from iris.app.tools.devices.esp32 import DeviceSensorsTool

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "flame": False,
                "distance_cm": 82, "distance_rear_cm": 15,
                "temperature_c": 28.4, "humidity_pct": 61,
            })

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(esp32_mod.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
        registry.add(Device(name="room sensor", base_url="http://192.168.1.72", kind="sensor"))

        temp = await DeviceSensorsTool(registry).execute(sensor="temperature")
        assert temp.success and temp.result["speech"] == "28.4 degrees."

        hum = await DeviceSensorsTool(registry).execute(sensor="humidity")
        assert hum.result["speech"] == "humidity 61%."

        both = await DeviceSensorsTool(registry).execute(sensor="climate")
        assert "28.4 degrees" in both.result["speech"]
        assert "humidity 61%" in both.result["speech"]

        dist = await DeviceSensorsTool(registry).execute(sensor="distance")
        assert dist.result["speech"] == "82 cm ahead, 15 cm behind."

    @pytest.mark.asyncio
    async def test_single_ultrasonic_still_reads_naturally(self, registry, monkeypatch):
        """The rear sensor is optional, so a one-sensor node must not say
        "82 cm ahead" with nothing behind it."""
        from iris.app.tools.devices.esp32 import DeviceSensorsTool

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"distance_cm": 82}))
        real_client = httpx.AsyncClient
        monkeypatch.setattr(esp32_mod.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
        registry.add(Device(name="front sensor", base_url="http://192.168.1.73", kind="sensor"))

        res = await DeviceSensorsTool(registry).execute(sensor="distance")
        assert res.result["speech"] == "nearest object 82 cm away."

    @pytest.mark.asyncio
    async def test_missing_dht_does_not_invent_a_temperature(self, registry, monkeypatch):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"motion": False, "motion_recent": False}))
        real_client = httpx.AsyncClient
        monkeypatch.setattr(esp32_mod.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))
        registry.add(Device(name="bare sensor", base_url="http://192.168.1.74", kind="sensor"))

        res = await DeviceSensorsTool(registry).execute(sensor="temperature")
        assert "no matching sensors" in res.result["speech"]

    @pytest.mark.parametrize("utterance,sensor", [
        ("is there any motion", "motion"),
        ("koi hai kya", "motion"),
        ("gas level kya hai", "gas"),
        ("kitna door hai", "distance"),
        ("check the sensors", "all"),
        ("what's the temperature", "temperature"),
        ("how hot is it", "temperature"),
        ("kitna garam hai", "temperature"),
        ("temperature batao", "temperature"),
        ("room temperature", "temperature"),
        ("what's the humidity", "humidity"),
        ("how humid is it", "humidity"),
        ("nami kitni hai", "humidity"),
    ])
    def test_sensor_nlu(self, utterance, sensor):
        match = IntentEngine().match(utterance)
        assert match and match.tool_name == "device_sensors"
        assert match.arguments.get("sensor") == sensor


# ---------------------------------------------------------------- servo
class TestServo:
    engine = IntentEngine()

    @pytest.mark.asyncio
    async def test_named_positions_become_angles(self, registry, fake_lan):
        from iris.app.tools.devices.esp32 import DeviceServoTool

        registry.add(Device(name="curtain", base_url="http://192.168.1.80", kind="relay"))
        tool = DeviceServoTool(registry)

        opened = await tool.execute(position="open")
        assert opened.success and opened.result["angle"] == 180
        assert any("/servo" in url and "angle=180" in url for url in fake_lan)

        closed = await tool.execute(position="close")
        assert closed.result["angle"] == 0

        half = await tool.execute(position="half")
        assert half.result["angle"] == 90

    @pytest.mark.asyncio
    async def test_hold_is_opt_in(self, registry, fake_lan):
        """A servo left powered fights its own gearbox, so holding is asked for
        explicitly and never assumed."""
        from iris.app.tools.devices.esp32 import DeviceServoTool

        registry.add(Device(name="curtain", base_url="http://192.168.1.80", kind="relay"))
        tool = DeviceServoTool(registry)

        await tool.execute(angle=45)
        assert not any("hold" in url for url in fake_lan)

        fake_lan.clear()
        await tool.execute(angle=45, hold=True)
        assert any("hold=1" in url for url in fake_lan)

    @pytest.mark.asyncio
    async def test_out_of_range_angle_refuses_instead_of_clamping(self, registry, fake_lan):
        """Clamping would park the horn against an end stop and let the servo
        stall there — that is how the gears strip."""
        from iris.app.tools.devices.esp32 import DeviceServoTool

        registry.add(Device(name="curtain", base_url="http://192.168.1.80", kind="relay"))
        res = await DeviceServoTool(registry).execute(angle=270)
        assert not res.success and "0 to 180" in res.error
        assert not fake_lan          # nothing was sent to the board

    @pytest.mark.asyncio
    async def test_angle_or_position_required(self, registry):
        from iris.app.tools.devices.esp32 import DeviceServoTool
        registry.add(Device(name="curtain", base_url="http://192.168.1.80", kind="relay"))
        res = await DeviceServoTool(registry).execute()
        assert not res.success

    @pytest.mark.asyncio
    async def test_no_node_explains_how_to_add_one(self, registry):
        from iris.app.tools.devices.esp32 import DeviceServoTool
        res = await DeviceServoTool(registry).execute(position="open")
        assert not res.success and "add device" in res.error

    @pytest.mark.parametrize("utterance,arguments", [
        ("open the curtain", {"position": "open"}),
        ("close the curtain", {"position": "close"}),
        ("shut the blinds", {"position": "close"}),
        ("open my curtains", {"position": "open"}),
        ("curtain kholo", {"position": "open"}),
        ("parda kholo", {"position": "open"}),
        ("curtain band karo", {"position": "close"}),
        ("open half the curtain", {"position": "half"}),
        ("open the curtain halfway", {"position": "half"}),
        ("set the servo to 45 degrees", {"angle": 45}),
        ("servo 90", {"angle": 90}),
        ("servo to 0", {"angle": 0}),
    ])
    def test_servo_nlu(self, utterance, arguments):
        match = self.engine.match(utterance)
        assert match and match.tool_name == "device_servo", utterance
        assert match.arguments == arguments

    @pytest.mark.parametrize("utterance", ["servo 181", "rotate servo 200", "servo 999"])
    def test_impossible_angle_is_not_silently_clamped_by_nlu(self, utterance):
        match = self.engine.match(utterance)
        assert match is None or match.tool_name != "device_servo"

    @pytest.mark.parametrize("utterance,tool", [
        ("open youtube", "open_website"),
        ("open notepad", "open_app"),
        ("turn on the kitchen light", "device_switch"),
        ("close chrome", "close_app"),
    ])
    def test_servo_rules_do_not_shadow_existing_commands(self, utterance, tool):
        match = self.engine.match(utterance)
        assert match and match.tool_name == tool


# --------------------------------------------------------- custom firmware mapping
class TestMapDeviceCommand:
    @pytest.mark.asyncio
    async def test_map_and_use_custom_command(self, registry, fake_lan):
        from iris.app.tools.devices.esp32 import MapDeviceCommandTool

        registry.add(Device(name="hall light", base_url="http://192.168.1.40", kind="relay"))
        res = await MapDeviceCommandTool(registry).execute(device="hall light", command="on", path="led/on")
        assert res.success
        assert registry.get("hall light").commands["on"] == "/led/on"

        switch_res = await DeviceSwitchTool(registry).execute(device="hall light", state="on")
        assert switch_res.success
        assert any(url.endswith("/led/on") for url in fake_lan)

    @pytest.mark.asyncio
    async def test_map_unknown_device_explains(self, registry):
        from iris.app.tools.devices.esp32 import MapDeviceCommandTool
        res = await MapDeviceCommandTool(registry).execute(device="ghost", command="on", path="/x")
        assert not res.success and "No device named" in res.error

    @pytest.mark.asyncio
    async def test_map_rejects_path_traversal(self, registry):
        from iris.app.tools.devices.esp32 import MapDeviceCommandTool
        registry.add(Device(name="fan", base_url="http://192.168.1.9"))
        res = await MapDeviceCommandTool(registry).execute(device="fan", command="on", path="/../etc")
        assert not res.success

    def test_map_command_nlu(self):
        cases = [
            ("map kitchen light on command to /led/on", "kitchen light", "on", "/led/on"),
            ("set fan off to /relay1/off", "fan", "off", "/relay1/off"),
        ]
        for utterance, device, command, path in cases:
            match = IntentEngine().match(utterance)
            assert match and match.tool_name == "map_device_command"
            assert match.arguments["device"] == device
            assert match.arguments["command"] == command
            assert match.arguments["path"] == path

    def test_map_command_does_not_collide_with_switch(self):
        match = IntentEngine().match("turn on the kitchen light")
        assert match and match.tool_name == "device_switch"
