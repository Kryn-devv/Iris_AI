"""Tests for the outbound node link — how a cloud IRIS reaches home hardware.

The link inverts the usual direction: the ESP32 dials in and holds a socket
open, because a board behind a home router has no reachable address. That makes
three things worth testing hard, and all three are failure modes that would
look like "the robot randomly stops working":

* **correlation** — many commands share one socket, so replies must reach the
  right waiter, and a timeout must not leave a caller hanging forever
* **untrusted input** — every frame comes from a device on someone's home
  network and may be malformed, oversized, or a flood
* **reconnection** — a half-open TCP connection through NAT looks perfectly
  alive from the server's side while the device has already given up
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from iris.app.core.bus import EventBus, Topics
from iris.app.nodes.link import (
    ALERT_KEYS,
    MAX_FRAME_BYTES,
    MIN_TELEMETRY_INTERVAL_S,
    STALE_AFTER_S,
    NodeLink,
    NodeLinkError,
    NodeLinkHub,
    _clean_readings,
    _is_true,
)
from iris.app.services.node_events import NodeEventService
from iris.app.tools.base import ToolError
from iris.app.tools.devices.registry import Device
from iris.app.tools.devices.transport import device_request


class FakeSocket:
    """Records what was sent, and can answer like a well-behaved node."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail_with: Exception | None = None

    async def send(self, frame: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(json.loads(frame))


@pytest.fixture()
def socket() -> FakeSocket:
    return FakeSocket()


@pytest.fixture()
def link(socket) -> NodeLink:
    return NodeLink(name="face", kind="face", send=socket.send, remote="1.2.3.4:5")


@pytest.fixture()
def hub() -> NodeLinkHub:
    return NodeLinkHub()


# --------------------------------------------------------------- correlation
class TestRequestReply:
    @pytest.mark.asyncio
    async def test_a_reply_reaches_its_waiter(self, link, socket):
        task = asyncio.create_task(link.request("/face", {"emotion": "happy"}))
        await asyncio.sleep(0)
        assert socket.sent[0]["path"] == "/face"
        assert socket.sent[0]["params"] == {"emotion": "happy"}
        link.resolve(socket.sent[0]["id"], True, {"ok": True})
        assert await task == {"ok": True}

    @pytest.mark.asyncio
    async def test_concurrent_requests_do_not_cross(self, link, socket):
        """Many commands share one socket; a mixed-up reply would show up as a
        relay switching when a motor was asked to move."""
        first = asyncio.create_task(link.request("/relay", {"ch": 1}))
        second = asyncio.create_task(link.request("/motor", {"dir": "forward"}))
        await asyncio.sleep(0)
        ids = {frame["path"]: frame["id"] for frame in socket.sent}
        # Answer out of order, exactly as a real node might.
        link.resolve(ids["/motor"], True, {"who": "motor"})
        link.resolve(ids["/relay"], True, {"who": "relay"})
        assert await first == {"who": "relay"}
        assert await second == {"who": "motor"}

    @pytest.mark.asyncio
    async def test_a_failed_reply_raises_with_the_nodes_reason(self, link, socket):
        task = asyncio.create_task(link.request("/face"))
        await asyncio.sleep(0)
        link.resolve(socket.sent[0]["id"], False, "unknown emotion")
        with pytest.raises(NodeLinkError, match="unknown emotion"):
            await task

    @pytest.mark.asyncio
    async def test_a_timeout_never_leaves_a_caller_hanging(self, link):
        with pytest.raises(NodeLinkError, match="did not answer"):
            await link.request("/face", timeout=0.05)

    @pytest.mark.asyncio
    async def test_a_timed_out_request_is_forgotten(self, link, socket):
        with pytest.raises(NodeLinkError):
            await link.request("/face", timeout=0.05)
        assert link._pending == {}, "a leak here grows without bound"
        # A late reply for a dead request must be harmless, not a crash.
        link.resolve(socket.sent[0]["id"], True, {"late": True})

    @pytest.mark.asyncio
    async def test_a_dead_socket_becomes_a_clear_error(self, link, socket):
        socket.fail_with = RuntimeError("socket closed")
        with pytest.raises(NodeLinkError, match="Could not reach"):
            await link.request("/face")

    @pytest.mark.asyncio
    async def test_closing_fails_every_in_flight_request(self, link):
        """Otherwise a spoken reply waits on a board that has already gone."""
        first = asyncio.create_task(link.request("/face", timeout=30))
        second = asyncio.create_task(link.request("/motor", timeout=30))
        await asyncio.sleep(0)
        link.close("power cut")
        for task in (first, second):
            with pytest.raises(NodeLinkError, match="disconnected"):
                await task

    @pytest.mark.asyncio
    async def test_a_closed_link_refuses_new_requests(self, link):
        link.close()
        with pytest.raises(NodeLinkError, match="not connected"):
            await link.request("/face")

    @pytest.mark.asyncio
    async def test_none_parameters_are_dropped_not_sent_as_null(self, link, socket):
        task = asyncio.create_task(link.request("/face", {"emotion": "happy", "look_x": None}))
        await asyncio.sleep(0)
        assert socket.sent[0]["params"] == {"emotion": "happy"}
        link.resolve(socket.sent[0]["id"], True, {})
        await task

    @pytest.mark.asyncio
    async def test_a_non_dict_reply_is_still_usable(self, link, socket):
        task = asyncio.create_task(link.request("/status"))
        await asyncio.sleep(0)
        link.resolve(socket.sent[0]["id"], True, "OK")
        assert await task == {"response": "OK"}


# ---------------------------------------------------------------- membership
class TestHubMembership:
    def test_register_and_get(self, hub, link):
        assert hub.register(link) is None
        assert hub.get("face") is link
        assert hub.count == 1

    def test_a_reconnect_replaces_the_old_link(self, hub, socket):
        """A half-open socket through NAT looks alive here and dead there, so
        the newest connection has to win or the node is unreachable forever."""
        old = NodeLink(name="face", kind="face", send=socket.send)
        new = NodeLink(name="face", kind="face", send=socket.send)
        hub.register(old)
        assert hub.register(new) is old
        assert hub.get("face") is new
        assert hub.count == 1

    def test_a_closed_link_is_not_handed_out(self, hub, link):
        hub.register(link)
        link.close()
        assert hub.get("face") is None

    def test_unregister_only_removes_its_own_link(self, hub, socket):
        old = NodeLink(name="face", kind="face", send=socket.send)
        new = NodeLink(name="face", kind="face", send=socket.send)
        hub.register(old)
        hub.register(new)
        hub.unregister(old, "replaced")     # the stale one closing late
        assert hub.get("face") is new, "the live link was evicted by a late close"

    def test_first_of_kind(self, hub, socket):
        hub.register(NodeLink(name="robot", kind="motor", send=socket.send))
        hub.register(NodeLink(name="face", kind="face", send=socket.send))
        assert hub.first_of_kind("motor").name == "robot"
        assert hub.first_of_kind("relay") is None

    def test_stale_links_are_dropped(self, hub, link):
        hub.register(link)
        link.last_seen = time.monotonic() - STALE_AFTER_S - 1
        assert link.stale is True
        assert hub.drop_stale() == [link]
        assert hub.count == 0

    def test_a_fresh_link_is_not_dropped(self, hub, link):
        hub.register(link)
        assert hub.drop_stale() == []

    @pytest.mark.asyncio
    async def test_requesting_an_absent_node_explains_why(self, hub):
        with pytest.raises(NodeLinkError, match="not connected"):
            await hub.request("face", "/face")

    def test_info_is_json_safe(self, hub, link):
        link.telemetry = {"motion": True, "gas_raw": 812}
        link.telemetry_at = time.monotonic()
        hub.register(link)
        json.dumps(hub.info())      # would raise if a value were not plain


# ------------------------------------------------------------ untrusted input
class TestFrameHandling:
    def test_a_reply_frame_resolves_a_request(self, hub, link):
        hub.register(link)
        future = asyncio.get_event_loop_policy().new_event_loop().create_future()
        link._pending[7] = future
        assert hub.handle_message(link, '{"type":"reply","id":7,"ok":true,"data":{"a":1}}') is None
        assert future.result() == {"a": 1}
        future.get_loop().close()

    def test_garbage_is_answered_not_crashed_on(self, hub, link):
        for junk in ("", "not json", "[1,2,3]", "null", "{", '{"type":123}'):
            reply = hub.handle_message(link, junk)
            assert reply is None or reply["type"] == "error"

    def test_an_oversized_frame_is_refused(self, hub, link):
        reply = hub.handle_message(link, "x" * (MAX_FRAME_BYTES + 1))
        assert reply == {"type": "error", "error": "frame too large"}

    def test_a_reply_without_an_id_is_refused(self, hub, link):
        reply = hub.handle_message(link, '{"type":"reply","ok":true}')
        assert reply["type"] == "error"

    def test_ping_is_answered_with_pong(self, hub, link):
        assert hub.handle_message(link, '{"type":"ping"}') == {"type": "pong"}

    def test_hello_records_what_the_node_says_it_is(self, hub, link):
        hub.handle_message(
            link,
            '{"type":"hello","firmware":"iris-s3-1.0","sensors":["motion","flame"]}',
        )
        assert link.firmware == "iris-s3-1.0"
        assert link.sensors == ["motion", "flame"]

    def test_an_unknown_frame_type_is_reported(self, hub, link):
        reply = hub.handle_message(link, '{"type":"launch_missiles"}')
        assert reply["type"] == "error"

    def test_any_frame_counts_as_a_sign_of_life(self, hub, link):
        link.last_seen = time.monotonic() - 60
        hub.handle_message(link, '{"type":"ping"}')
        assert link.stale is False

    def test_readings_are_cleaned_of_anything_odd(self):
        cleaned = _clean_readings({
            "motion": True, "gas_raw": 812, "temp": 22.5, "label": "ok",
            "nested": {"a": 1}, "listy": [1, 2, 3], "none": None,
            "x" * 100: 1, "long": "y" * 200,
        })
        assert cleaned["motion"] is True
        assert cleaned["gas_raw"] == 812
        assert cleaned["temp"] == 22.5
        assert cleaned["label"] == "ok"
        assert "nested" not in cleaned and "listy" not in cleaned and "none" not in cleaned
        assert all(len(k) <= 32 for k in cleaned)
        assert len(cleaned["long"]) <= 64

    def test_a_flood_of_keys_is_capped(self):
        cleaned = _clean_readings({f"k{i}": i for i in range(100)})
        assert len(cleaned) <= 32

    @pytest.mark.parametrize("value,expected", [
        (True, True), (False, False), (1, True), (0, False), (-1, False),
        (0.5, True), ("1", True), ("true", True), ("YES", True), ("alarm", True),
        ("0", False), ("false", False), ("", False), (None, False), ([], False),
    ])
    def test_sensor_truthiness(self, value, expected):
        """A sensor's 0 means "no", so Python's truthiness is not good enough."""
        assert _is_true(value) is expected


# ------------------------------------------------------------------ telemetry
class TestTelemetry:
    def test_telemetry_is_stored_and_observed(self, hub, link):
        seen = []
        hub.set_observers(on_telemetry=lambda l, r: seen.append((l.name, r)))
        hub.handle_message(link, '{"type":"telemetry","sensors":{"motion":true,"gas_raw":700}}')
        assert link.telemetry["motion"] is True
        assert seen == [("face", {"motion": True, "gas_raw": 700})]

    def test_telemetry_merges_rather_than_replaces(self, hub, link):
        hub.handle_message(link, '{"type":"telemetry","sensors":{"motion":true}}')
        link.telemetry_at = 0.0                       # allow the next one through
        hub.handle_message(link, '{"type":"telemetry","sensors":{"gas_raw":700}}')
        assert link.telemetry == {"motion": True, "gas_raw": 700}

    def test_a_flood_does_not_flood_the_bus(self, hub, link):
        """A node with a runaway loop must not be able to spam every UI."""
        published = []
        hub.set_observers(on_telemetry=lambda l, r: published.append(r))
        for i in range(50):
            hub.handle_message(link, '{"type":"telemetry","sensors":{"gas_raw":%d}}' % i)
        assert len(published) == 1, f"published {len(published)} times"
        # ...but the newest values are still kept for whoever asks.
        assert link.telemetry["gas_raw"] == 49

    def test_the_rate_limit_lets_the_next_real_reading_through(self, hub, link):
        published = []
        hub.set_observers(on_telemetry=lambda l, r: published.append(r))
        hub.handle_message(link, '{"type":"telemetry","sensors":{"gas_raw":1}}')
        link.telemetry_at -= MIN_TELEMETRY_INTERVAL_S + 0.1
        hub.handle_message(link, '{"type":"telemetry","sensors":{"gas_raw":2}}')
        assert len(published) == 2

    def test_non_dict_telemetry_is_ignored(self, hub, link):
        hub.handle_message(link, '{"type":"telemetry","sensors":"lots"}')
        assert link.telemetry == {}

    def test_an_observer_that_raises_does_not_kill_the_link(self, hub, link):
        def boom(_link, _readings):
            raise RuntimeError("bus exploded")

        hub.set_observers(on_telemetry=boom)
        hub.handle_message(link, '{"type":"telemetry","sensors":{"motion":true}}')
        assert link.telemetry["motion"] is True


# --------------------------------------------------------------------- alerts
class TestAlerts:
    def test_an_explicit_alert_is_observed(self, hub, link):
        alerts = []
        hub.set_observers(on_alert=lambda l, a: alerts.append(a))
        hub.handle_message(link, '{"type":"alert","kind":"flame","message":"hot"}')
        assert alerts[0]["kind"] == "flame"
        assert alerts[0]["node"] == "face"
        assert alerts[0]["source"] == "node"

    @pytest.mark.parametrize("key", ALERT_KEYS)
    def test_a_reading_crossing_into_danger_raises_its_own_alert(self, hub, link, key):
        """Older firmware may only push readings and never send an alert frame.
        A flame is not something to find out about when someone next asks."""
        alerts = []
        hub.set_observers(on_alert=lambda l, a: alerts.append(a))
        hub.handle_message(link, '{"type":"telemetry","sensors":{"%s":false}}' % key)
        assert alerts == []
        link.telemetry_at = 0.0
        hub.handle_message(link, '{"type":"telemetry","sensors":{"%s":true}}' % key)
        assert [a["kind"] for a in alerts] == [key]
        assert alerts[0]["source"] == "telemetry"

    def test_a_reading_that_stays_dangerous_alerts_once(self, hub, link):
        alerts = []
        hub.set_observers(on_alert=lambda l, a: alerts.append(a))
        for _ in range(5):
            link.telemetry_at = 0.0
            hub.handle_message(link, '{"type":"telemetry","sensors":{"flame":true}}')
        assert len(alerts) == 1, "an alert per reading would never stop talking"

    def test_clearing_then_returning_alerts_again(self, hub, link):
        alerts = []
        hub.set_observers(on_alert=lambda l, a: alerts.append(a))
        for value in ("true", "false", "true"):
            link.telemetry_at = 0.0
            hub.handle_message(link, '{"type":"telemetry","sensors":{"flame":%s}}' % value)
        assert len(alerts) == 2

    def test_an_alert_observer_that_raises_does_not_kill_the_link(self, hub, link):
        hub.set_observers(on_alert=lambda l, a: (_ for _ in ()).throw(RuntimeError("boom")))
        hub.handle_message(link, '{"type":"alert","kind":"flame"}')     # must not raise

    def test_alert_fields_are_truncated(self, hub, link):
        alerts = []
        hub.set_observers(on_alert=lambda l, a: alerts.append(a))
        hub.handle_message(
            link,
            json.dumps({"type": "alert", "kind": "x" * 100, "message": "y" * 500}),
        )
        assert len(alerts[0]["kind"]) <= 32
        assert len(alerts[0]["message"]) <= 200


class TestNodeEventService:
    @pytest.mark.asyncio
    async def test_telemetry_reaches_the_bus(self, hub, link):
        bus = EventBus()
        service = NodeEventService(hub=hub, bus=bus)
        await service.start()
        sub = bus.subscribe([Topics.NODE_TELEMETRY])
        try:
            hub.handle_message(link, '{"type":"telemetry","sensors":{"motion":true}}')
            event = sub.queue.get_nowait()
        finally:
            await service.stop()
        assert event.payload["node"] == "face"
        assert event.payload["sensors"]["motion"] is True

    @pytest.mark.asyncio
    async def test_an_alert_reaches_the_bus(self, hub, link, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.NODE_ALERTS_SPOKEN", False)
        bus = EventBus()
        service = NodeEventService(hub=hub, bus=bus)
        await service.start()
        sub = bus.subscribe([Topics.NODE_ALERT])
        try:
            hub.handle_message(link, '{"type":"alert","kind":"flame"}')
            event = sub.queue.get_nowait()
        finally:
            await service.stop()
        assert event.payload["kind"] == "flame"

    @pytest.mark.asyncio
    async def test_repeated_alerts_are_spoken_once(self, hub, link, monkeypatch):
        """A sensor sitting on its threshold flickers; a voice repeating a fire
        warning every second is worse than useless."""
        spoken = []
        monkeypatch.setattr("iris.app.core.config.settings.NODE_ALERTS_SPOKEN", True)
        service = NodeEventService(hub=hub, bus=EventBus())
        await service.start()
        monkeypatch.setattr(service, "_speak_later",
                            lambda sentence, emotion: spoken.append(sentence))
        try:
            for _ in range(6):
                hub.handle_message(link, '{"type":"alert","kind":"flame"}')
        finally:
            await service.stop()
        assert len(spoken) == 1
        assert "flame" in spoken[0].lower() or "fire" in spoken[0].lower()

    @pytest.mark.asyncio
    async def test_different_alert_kinds_are_each_announced(self, hub, link, monkeypatch):
        spoken = []
        monkeypatch.setattr("iris.app.core.config.settings.NODE_ALERTS_SPOKEN", True)
        service = NodeEventService(hub=hub, bus=EventBus())
        await service.start()
        monkeypatch.setattr(service, "_speak_later",
                            lambda sentence, emotion: spoken.append(sentence))
        try:
            hub.handle_message(link, '{"type":"alert","kind":"flame"}')
            hub.handle_message(link, '{"type":"alert","kind":"gas"}')
        finally:
            await service.stop()
        assert len(spoken) == 2

    @pytest.mark.asyncio
    async def test_stop_is_safe_before_start_and_twice(self, hub):
        service = NodeEventService(hub=hub, bus=EventBus())
        await service.stop()
        await service.start()
        await service.stop()
        await service.stop()


# ------------------------------------------------------------------ transport
class TestTransportDispatch:
    """A tool asks for a path; the transport decides how it travels. Nothing
    above this line changes when IRIS moves to a VPS."""

    @pytest.mark.asyncio
    async def test_a_linked_device_goes_over_its_socket(self, hub, link, socket):
        hub.register(link)
        device = Device(name="face", kind="face", transport="link")
        task = asyncio.create_task(device_request(device, "/face", {"emotion": "sad"}, hub=hub))
        await asyncio.sleep(0)
        assert socket.sent[0]["path"] == "/face"
        link.resolve(socket.sent[0]["id"], True, {"ok": True})
        assert await task == {"ok": True}

    @pytest.mark.asyncio
    async def test_a_missing_path_slash_is_added(self, hub, link, socket):
        hub.register(link)
        device = Device(name="face", kind="face", transport="link")
        task = asyncio.create_task(device_request(device, "status", hub=hub))
        await asyncio.sleep(0)
        assert socket.sent[0]["path"] == "/status"
        link.resolve(socket.sent[0]["id"], True, {})
        await task

    @pytest.mark.asyncio
    async def test_an_offline_linked_device_gives_a_useful_error(self, hub):
        device = Device(name="face", kind="face", transport="link")
        with pytest.raises(ToolError) as excinfo:
            await device_request(device, "/face", hub=hub)
        assert "not connected" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_a_lan_device_with_no_address_is_a_clear_error(self, hub):
        device = Device(name="fan", kind="relay", transport="lan")
        device.base_url = ""
        with pytest.raises(ToolError, match="no address"):
            await device_request(device, "/relay", hub=hub)
