"""Tests for the ESP32 / smart-device control layer."""

from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest

from iris.app.tools.devices.registry import (
    DEVICE_KINDS,
    Device,
    DeviceError,
    DeviceRegistry,
    normalize_base_url,
    normalize_name,
)
from iris.app.tools.base import ToolError
from iris.app.tools.devices import esp32 as esp32_mod
from iris.app.tools.devices import transport as transport_mod
from iris.app.tools.devices.esp32 import (
    DeviceCommandTool,
    DeviceMotorTool,
    DeviceStatusTool,
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
            return httpx.Response(200, json={"name": "node", "kind": "motor", "motors": True})
        if path == "/motor":
            return httpx.Response(200, json={"motor": request.url.params["dir"]})
        if path == "/go":
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
        r1.add(Device(name="fan", base_url="http://192.168.1.9", kind="generic"))
        r2 = DeviceRegistry(path=path)
        assert [d.name for d in r2.list()] == ["fan"]

    def test_fuzzy_lookup(self, registry):
        registry.add(Device(name="kitchen light", base_url="http://192.168.1.5", kind="generic"))
        assert registry.get("light").name == "kitchen light"
        assert registry.get("the kitchen light").name == "kitchen light"
        registry.add(Device(name="bedroom light", base_url="http://192.168.1.6", kind="generic"))
        # ambiguous now
        assert registry.get("light") is None


# ------------------------------------------------------------------- tools
class TestDeviceTools:
    @pytest.mark.asyncio
    async def test_register_and_list(self, registry, fake_lan):
        reg = RegisterDeviceTool(registry)
        res = await reg.execute(name="Robot", address="192.168.1.50", kind="motor")
        assert res.success and res.result["reachable"]
        listing = await ListDevicesTool(registry).execute()
        assert listing.result["count"] == 1

    @pytest.mark.asyncio
    async def test_register_rejects_public_address(self, registry):
        res = await RegisterDeviceTool(registry).execute(name="evil", address="8.8.8.8")
        assert not res.success and "local network" in res.error

    def test_a_stored_relay_kind_loads_as_generic(self, tmp_path):
        """devices.json written before the relay node was retired still loads;
        the retired kind just stops meaning anything special."""
        path = tmp_path / "devices.json"
        path.write_text('{"devices": [{"name": "fan", "base_url": "http://192.168.1.9", '
                        '"kind": "relay", "default_channel": 2}]}')
        r = DeviceRegistry(path=path)
        assert [(d.name, d.kind) for d in r.list()] == [("fan", "generic")]

    @pytest.mark.asyncio
    async def test_motor_uses_custom_command_map(self, registry, fake_lan):
        registry.add(Device(name="robot", base_url="http://192.168.1.8",
                            kind="motor", commands={"forward": "/go"}))
        res = await DeviceMotorTool(registry).execute(action="forward")
        assert res.success
        assert any(url.endswith("/go") for url in fake_lan)

    @pytest.mark.asyncio
    async def test_motor_unknown_device_is_helpful(self, registry):
        registry.add(Device(name="robot", base_url="http://192.168.1.60", kind="motor"))
        res = await DeviceMotorTool(registry).execute(action="forward", device="rover")
        assert not res.success and "add device rover" in res.error

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
        ("add device robot at 192.168.1.50 as motor", "register_device",
         {"name": "robot", "address": "192.168.1.50", "kind": "motor"}),
        ("add device face at 192.168.1.70 as face", "register_device",
         {"name": "face", "address": "192.168.1.70", "kind": "face"}),
        ("robot forward", "device_motor", {"action": "forward"}),
        ("move the robot left", "device_motor", {"action": "left"}),
        ("robot peeche", "device_motor", {"action": "backward"}),
        ("stop the robot", "device_motor", {"action": "stop"}),
        ("list my devices", "list_devices", {}),
        ("is the robot online", "device_status", {"device": "robot"}),
    ])
    def test_device_phrases_route(self, utterance, tool, expected):
        match = self.engine.match(utterance)
        assert match is not None, f"no match for {utterance!r}"
        assert match.tool_name == tool
        for key, value in expected.items():
            assert match.arguments.get(key) == value

    @pytest.mark.parametrize("utterance", [
        # The relay node is retired, so appliance talk must not be routed to
        # any device tool — least of all the robot. It goes to the LLM, which
        # can say there is no such device, instead of a motor command that a
        # light switch never meant.
        "turn on the kitchen light", "switch off the fan", "lights on",
        "fan band karo", "toggle the socket", "open the curtain", "servo 90",
    ])
    def test_appliance_phrases_do_not_reach_a_device_tool(self, utterance):
        match = self.engine.match(utterance)
        assert match is None or not match.tool_name.startswith("device_"), utterance

    @pytest.mark.parametrize("kind", list(DEVICE_KINDS))
    def test_every_registry_kind_can_be_typed(self, kind):
        """The NLU must accept every kind the registry accepts.

        It once listed fewer, so "add device face at <ip> as face" — the exact
        line docs/ESP32.md tells people to type — matched nothing and fell
        through to the LLM instead of registering a device.
        """
        match = self.engine.match(f"add device myboard at 192.168.1.50 as {kind}")
        assert match and match.tool_name == "register_device", kind
        assert match.arguments.get("kind") == kind

    def test_kind_is_optional(self):
        match = self.engine.match("add device myboard at 192.168.1.50")
        assert match and match.tool_name == "register_device"
        assert "kind" not in match.arguments

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
            if request.url.path == "/status":          # the one fast-path probe
                return httpx.Response(200, json={"kind": "sensor"})
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
        assert res.result["speech"] == "nearest object 82 cm ahead."

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


# ----------------------------------------------------- gas from the module's DO
class TestGasWithoutALevel:
    """The MQ-2's analog pin may sit on an ADC2 GPIO the S3 cannot read under
    WiFi, so the board can report the module's own yes/no output alone."""

    def test_alarm_without_a_level(self):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        assert DeviceSensorsTool._summarize({"gas_do": True, "gas_alarm": True}, "gas") == "GAS ALARM."

    def test_quiet_without_a_level(self):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        assert DeviceSensorsTool._summarize({"gas_do": False, "gas_alarm": False}, "gas") == "no gas detected."

    def test_a_level_still_reads_as_before(self):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        assert DeviceSensorsTool._summarize({"gas_raw": 900, "gas_alarm": False}, "gas") == "gas level 900 (normal)."


# ------------------------------------------------------ four distance sensors
class TestFourDistances:
    def test_two_pairs_read_as_one_sentence(self):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        data = {"distance_cm": 40, "distance_rear_cm": 12,
                "distances": {"front_left": 44, "front_right": 40, "rear_left": 12, "rear_right": 90}}
        assert DeviceSensorsTool._summarize(data, "distance") == "40 cm ahead, 12 cm behind on the left."

    def test_a_side_is_named_only_when_it_matters(self):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        data = {"distance_cm": 40, "distance_rear_cm": 50,
                "distances": {"front_left": 41, "front_right": 40, "rear_left": 50, "rear_right": 55}}
        assert DeviceSensorsTool._summarize(data, "distance") == "40 cm ahead, 50 cm behind."

    def test_one_dead_sensor_does_not_hide_the_other(self):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        data = {"distance_cm": 30,
                "distances": {"front_left": None, "front_right": 30, "rear_left": None, "rear_right": None}}
        assert DeviceSensorsTool._summarize(data, "distance") == "nearest object 30 cm ahead on the right."

    def test_older_firmware_with_two_sensors_still_reads(self):
        from iris.app.tools.devices.esp32 import DeviceSensorsTool
        assert DeviceSensorsTool._summarize({"distance_cm": 82, "distance_rear_cm": 15}, "distance") \
            == "82 cm ahead, 15 cm behind."
        assert DeviceSensorsTool._summarize({"distance_cm": 82}, "all") == "nearest object 82 cm ahead."


# --------------------------------------------------------- custom firmware mapping
class TestMapDeviceCommand:
    @pytest.mark.asyncio
    async def test_map_and_use_custom_command(self, registry, fake_lan):
        from iris.app.tools.devices.esp32 import MapDeviceCommandTool

        registry.add(Device(name="rover", base_url="http://192.168.1.40", kind="motor"))
        res = await MapDeviceCommandTool(registry).execute(device="rover", command="forward", path="go")
        assert res.success
        assert registry.get("rover").commands["forward"] == "/go"

        drive = await DeviceMotorTool(registry).execute(action="forward", device="rover")
        assert drive.success
        assert any(url.endswith("/go") for url in fake_lan)

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
            ("map rover forward command to /go", "rover", "forward", "/go"),
            ("set rover stop to /halt", "rover", "stop", "/halt"),
        ]
        for utterance, device, command, path in cases:
            match = IntentEngine().match(utterance)
            assert match and match.tool_name == "map_device_command"
            assert match.arguments["device"] == device
            assert match.arguments["command"] == command
            assert match.arguments["path"] == path

    def test_map_command_does_not_collide_with_driving(self):
        match = IntentEngine().match("move the robot forward")
        assert match and match.tool_name == "device_motor"


# ------------------------------------------------------------ the fast path
class _FakeNodeUdp(asyncio.DatagramProtocol):
    """A stand-in for the robot node's UDP command listener (fastlink.h)."""

    def __init__(self, answer=None, silent=False, raw=None):
        self.seen: list[str] = []
        self._answer = answer if answer is not None else {"motor": "forward"}
        self._silent = silent
        self._raw = raw
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data, addr):
        self.seen.append(data.decode())
        if self._silent:
            return                      # a board that never answers
        body = self._raw if self._raw is not None else json.dumps(self._answer).encode()
        self._transport.sendto(body, addr)


async def _serve_udp(silent=False, answer=None, raw=None):
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        lambda: _FakeNodeUdp(answer=answer, silent=silent, raw=raw),
        local_addr=("127.0.0.1", 0),
    )
    return transport, proto, transport.get_extra_info("sockname")[1]


def _mock_http(monkeypatch, handler):
    """Point the LAN transport at a handler, returning the URLs it saw."""
    seen: list[str] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    mock = httpx.MockTransport(wrapped)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        transport_mod.httpx, "AsyncClient",
        lambda **kw: real(**{**kw, "transport": mock}),
    )
    return seen


class TestFastPath:
    """The UDP command path: how it is discovered, used and given up on."""

    def test_port_is_read_from_what_the_node_advertises(self):
        assert transport_mod.read_fast_port({"fast": {"udp": 8267}}) == 8267
        assert transport_mod.read_fast_port({"fast": {"udp": "8267"}}) == 8267

    def test_a_node_that_does_not_advertise_gets_no_datagram(self):
        # Every one of these must read as "no fast path" rather than as a
        # port, because guessing one means firing a stray packet at whatever
        # the user actually has on that port.
        for status in (
            {},                                   # older firmware
            {"fast": None},
            {"fast": {}},
            {"fast": {"udp": None}},
            {"fast": {"udp": "soon"}},
            {"fast": {"udp": 0}},                 # out of range
            {"fast": {"udp": 70000}},
            "not even a dict",
        ):
            assert transport_mod.read_fast_port(status) is None

    @pytest.mark.asyncio
    async def test_udp_command_round_trip(self):
        srv, node, port = await _serve_udp(answer={"motor": "forward", "speed": 200})
        try:
            answer = await transport_mod.udp_command(
                "127.0.0.1", port, "/motor?dir=forward&speed=200"
            )
        finally:
            srv.close()
        assert answer == {"motor": "forward", "speed": 200}
        assert node.seen == ["/motor?dir=forward&speed=200"]

    @pytest.mark.asyncio
    async def test_non_json_answer_is_carried_not_raised(self):
        """Custom firmware answering plain text must read as a reply, not as
        a transport failure that sends the same command a second time."""
        srv, _node, port = await _serve_udp(raw=b"OK, moving")
        try:
            answer = await transport_mod.udp_command("127.0.0.1", port, "/motor?dir=forward")
        finally:
            srv.close()
        assert answer == {"response": "OK, moving"}

    @pytest.mark.asyncio
    async def test_a_bare_json_value_is_wrapped(self):
        srv, _node, port = await _serve_udp(raw=b"42")
        try:
            answer = await transport_mod.udp_command("127.0.0.1", port, "/status")
        finally:
            srv.close()
        assert answer == {"response": 42}

    @pytest.mark.asyncio
    async def test_silence_raises_rather_than_hanging(self):
        srv, _node, port = await _serve_udp(silent=True)
        try:
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                await transport_mod.udp_command(
                    "127.0.0.1", port, "/motor?dir=forward", timeout=0.05
                )
        finally:
            srv.close()

    @pytest.mark.asyncio
    async def test_commands_go_by_udp_once_the_node_advertises_one(self, monkeypatch):
        srv, node, port = await _serve_udp(answer={"motor": "forward"})
        try:
            http = _mock_http(monkeypatch, lambda r: httpx.Response(
                200, json={"name": "robot", "kind": "motor", "fast": {"udp": port}}))
            dev = Device(name="robot", base_url="http://127.0.0.1", kind="motor")

            first = await transport_mod.device_request(
                dev, "/motor", {"dir": "forward", "speed": 200})
            second = await transport_mod.device_request(dev, "/motor", {"dir": "stop"})
        finally:
            srv.close()

        assert first == {"motor": "forward"}
        assert second == {"motor": "forward"}
        # Both commands arrived as datagrams, carrying their parameters.
        assert node.seen == ["/motor?dir=forward&speed=200", "/motor?dir=stop"]
        # HTTP was used exactly once, to ask the board what port to use.
        assert http == ["http://127.0.0.1/status"]

    @pytest.mark.asyncio
    async def test_an_unanswered_datagram_falls_back_to_http(self, monkeypatch):
        """A lost datagram must not become a lost command."""
        srv, node, port = await _serve_udp(silent=True)
        monkeypatch.setattr(transport_mod, "FAST_TIMEOUT", 0.05)
        try:
            http = _mock_http(monkeypatch, lambda r: httpx.Response(
                200, json={"fast": {"udp": port}, "motor": "forward"}))
            dev = Device(name="robot", base_url="http://127.0.0.1", kind="motor")
            answer = await transport_mod.device_request(dev, "/motor", {"dir": "forward"})
        finally:
            srv.close()

        assert answer["motor"] == "forward"
        assert node.seen == ["/motor?dir=forward"]          # it was tried
        assert any("dir=forward" in url for url in http)    # and then done over HTTP

    @pytest.mark.asyncio
    async def test_a_generic_board_is_never_interrogated_on_the_off_chance(self, monkeypatch):
        """Only kinds whose firmware has a listener are worth asking. A board
        of unknown make must not pay a round trip to find out it has no fast
        path — it would pay that timeout on every command."""
        http = _mock_http(monkeypatch, lambda r: httpx.Response(
            200, json={"name": "thing", "kind": "generic"}))
        dev = Device(name="thing", base_url="http://127.0.0.1", kind="generic")

        await transport_mod.device_request(dev, "/go", {"x": 1})
        await transport_mod.device_request(dev, "/go", {"x": 2})

        assert not any("/status" in u for u in http)
        assert any("x=1" in u for u in http) and any("x=2" in u for u in http)

    @pytest.mark.asyncio
    async def test_the_face_board_gets_the_fast_path_too(self, monkeypatch):
        """An expression should land as fast as a drive command: the S3
        firmware has the same UDP listener, so a face node is asked once and
        its datagrams go straight to the board from then on."""
        srv, node, port = await _serve_udp(answer={"ok": True, "face": {"emotion": "happy"}})
        try:
            _mock_http(monkeypatch, lambda r: httpx.Response(
                200, json={"kind": "face", "fast": {"udp": port}}))
            dev = Device(name="face", base_url="http://127.0.0.1", kind="face")
            await transport_mod.device_request(dev, "/face", {"emotion": "happy"})
        finally:
            srv.close()
        assert node.seen == ["/face?emotion=happy"]

    @pytest.mark.asyncio
    async def test_a_motor_node_on_older_firmware_is_asked_once_only(self, monkeypatch):
        http = _mock_http(monkeypatch, lambda r: httpx.Response(
            200, json={"name": "robot", "kind": "motor", "motors": True}))
        dev = Device(name="robot", base_url="http://127.0.0.1", kind="motor")

        await transport_mod.device_request(dev, "/motor", {"dir": "forward"})
        await transport_mod.device_request(dev, "/motor", {"dir": "stop"})

        # The "no fast path" answer is remembered, so the board is asked once
        # and every command after that goes straight out over HTTP.
        assert http.count("http://127.0.0.1/status") == 1
        assert any("dir=forward" in u for u in http) and any("dir=stop" in u for u in http)

    @pytest.mark.asyncio
    async def test_any_kind_picks_up_a_fast_path_it_advertises(self, monkeypatch):
        """No round trip is spent asking a generic board, but if one of its own
        /status answers mentions a port, it gets datagrams from then on. That
        is what stops this module from having to be edited when another
        firmware grows the listener."""
        srv, node, port = await _serve_udp(answer={"ok": True})
        try:
            _mock_http(monkeypatch, lambda r: httpx.Response(
                200, json={"kind": "generic", "fast": {"udp": port}}))
            dev = Device(name="thing", base_url="http://127.0.0.1", kind="generic")

            await transport_mod.device_request(dev, "/status")     # over HTTP
            assert node.seen == []
            await transport_mod.device_request(dev, "/go", {"x": 1})
        finally:
            srv.close()
        assert node.seen == ["/go?x=1"]

    @pytest.mark.asyncio
    async def test_a_board_that_is_off_does_not_cost_two_timeouts_per_command(
        self, monkeypatch
    ):
        """A failed probe is held off briefly, not repeated. Without that, a
        board that is simply unplugged made every spoken command pay the probe
        timeout AND the command timeout before the user heard anything."""
        probes = {"n": 0}

        def handler(request):
            if request.url.path == "/status":
                probes["n"] += 1
            raise httpx.ConnectError("no route to host")

        _mock_http(monkeypatch, handler)
        dev = Device(name="robot", base_url="http://127.0.0.1", kind="motor")

        for _ in range(3):
            with pytest.raises(ToolError):
                await transport_mod.device_request(dev, "/motor", {"dir": "forward"})
        assert probes["n"] == 1

    @pytest.mark.asyncio
    async def test_a_board_that_comes_back_is_picked_up_again(self, monkeypatch):
        """The hold-off is seconds, not permanent: a board unplugged when we
        first asked must still get the fast path once it is back."""
        srv, node, port = await _serve_udp(answer={"motor": "forward"})
        monkeypatch.setattr(transport_mod, "FAST_PROBE_RETRY_S", 0.05)
        try:
            state = {"up": False}

            def handler(_request):
                if not state["up"]:
                    raise httpx.ConnectError("no route to host")
                return httpx.Response(200, json={"fast": {"udp": port}})

            _mock_http(monkeypatch, handler)
            dev = Device(name="robot", base_url="http://127.0.0.1", kind="motor")

            with pytest.raises(ToolError):
                await transport_mod.device_request(dev, "/motor", {"dir": "forward"})
            assert node.seen == []                  # nothing guessed at while down

            state["up"] = True
            await asyncio.sleep(0.06)               # hold-off expires
            await transport_mod.device_request(dev, "/motor", {"dir": "forward"})
        finally:
            srv.close()
        assert node.seen == ["/motor?dir=forward"]

    @pytest.mark.asyncio
    async def test_a_command_gets_a_tighter_budget_than_a_reading(self, monkeypatch):
        """A turn waiting on /motor must not be held for as long as a /status
        that is still worth having when it is slow."""
        seen: list = []

        def fake_client(url, timeout):
            seen.append((url, timeout.read, timeout.connect))
            return httpx.AsyncClient(
                timeout=timeout, transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, json={"ok": True})))

        monkeypatch.setattr(transport_mod, "_client", fake_client)
        dev = Device(name="robot", base_url="http://127.0.0.1", kind="generic")

        await transport_mod.device_request(dev, "/motor", {"dir": "forward"})
        await transport_mod.device_request(dev, "/status")

        command, reading = seen[0], seen[-1]
        assert command[1] == transport_mod.COMMAND_TIMEOUT.read
        assert reading[1] == transport_mod.LAN_TIMEOUT.read
        assert command[1] < reading[1] and command[2] < reading[2]

    def test_an_unresolvable_name_is_told_apart_from_a_board_that_is_off(self):
        """'is it powered on?' sends the user to check the wrong thing when
        the real problem is that this machine cannot resolve .local at all."""
        gai = socket.gaierror(-2, "Name or service not known")
        wrapped = httpx.ConnectError("nope")
        wrapped.__cause__ = gai
        assert transport_mod._looks_unresolvable(wrapped)
        assert not transport_mod._looks_unresolvable(
            httpx.ConnectError("connection refused"))

    @pytest.mark.asyncio
    async def test_device_calls_never_go_through_a_configured_proxy(self, monkeypatch):
        """Device addresses are LAN-only by construction, so a proxy could
        never serve them — and reading the environment for one was also 26 ms
        of the 43 ms every command used to spend building a client."""
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
        client = transport_mod._client("http://192.168.1.60/motor",
                                       transport_mod.COMMAND_TIMEOUT)
        try:
            assert client.trust_env is False
        finally:
            await client.aclose()
