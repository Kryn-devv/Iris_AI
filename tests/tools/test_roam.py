"""The robot deciding for itself — tested without a robot.

``decide()`` is pure: distances in, one choice out. That is the point of the
design, because the alternative is discovering the rules by driving a real
machine into a real table.

The safety properties are tested as hard as the behaviour. This code makes a
physical object move with nobody's hand on it, so "it stops when it cannot
see" matters more than "it explores nicely".
"""

from __future__ import annotations

import asyncio
import random

import pytest

from iris.app.core.config import settings
from iris.app.services.roam import (
    FAILURES_BEFORE_PARK,
    WEDGE_MIN_MOVES,
    WEDGE_WINDOW,
    RoamService,
    read_pair,
)
from iris.app.tools.devices.registry import Device, DeviceRegistry


def sensors(fl=None, fr=None, rl=None, rr=None, **extra):
    """A /sensors payload with whatever the board reported."""
    distances = {k: v for k, v in
                 (("front_left", fl), ("front_right", fr),
                  ("rear_left", rl), ("rear_right", rr)) if v is not None}
    payload = {"distances": distances} if distances else {}
    payload.update(extra)
    return payload


@pytest.fixture()
def service():
    registry = DeviceRegistry()
    registry.add(Device(name="robot", kind="motor", base_url="http://127.0.0.1"))
    registry.add(Device(name="face", kind="face", base_url="http://127.0.0.2"))
    clock = [1000.0]
    svc = RoamService(registry=registry, clock=lambda: clock[0],
                      rng=random.Random(7), speak=_noop)
    svc._clock_list = clock          # tests advance time through this
    return svc


async def _noop(_sentence):
    return None


class TestReadingTheRoom:
    def test_both_front_sensors_are_read(self):
        assert read_pair(sensors(fl=30, fr=80), "front") == (30.0, 80.0)

    def test_a_missing_sensor_is_none_not_zero(self):
        """Zero would read as 'something is touching me' and trigger an escape."""
        assert read_pair(sensors(fl=30), "front") == (30.0, None)

    def test_a_negative_reading_is_no_reading(self):
        """The firmware reports -1 for a sensor that did not echo."""
        assert read_pair(sensors(fl=-1, fr=50), "front") == (None, 50.0)

    def test_an_older_single_value_board_still_works(self):
        assert read_pair({"distance_cm": 42}, "front") == (42.0, 42.0)
        assert read_pair({"distance_rear_cm": 17}, "rear") == (17.0, 17.0)


class TestItDecides:
    def test_a_clear_room_means_go(self, service):
        d = service.decide(sensors(fl=200, fr=210, rl=200, rr=200))
        assert d.behaviour == "cruise" and d.action == "forward"

    def test_something_close_ahead_means_steer_around_it(self, service):
        d = service.decide(sensors(fl=30, fr=120))
        assert d.behaviour == "avoid"
        assert d.action == "right", "the left sensor is closer, so the room is on the right"

    def test_it_steers_toward_the_side_with_more_room(self, service):
        """Two front sensors instead of one is what makes this a decision."""
        assert service.decide(sensors(fl=120, fr=30)).action == "left"
        service._front_history.clear()
        assert service.decide(sensors(fl=30, fr=120)).action == "right"

    def test_something_right_there_means_back_off(self, service):
        d = service.decide(sensors(fl=12, fr=15, rl=90, rr=90))
        assert d.behaviour == "escape" and d.action == "backward"

    def test_boxed_in_it_turns_instead_of_reversing_into_the_wall_behind(self, service):
        d = service.decide(sensors(fl=12, fr=15, rl=14, rr=13))
        assert d.behaviour == "escape"
        assert d.action in ("left", "right"), "there is no room behind to reverse into"

    def test_silent_front_sensors_never_produce_forward(self, service):
        """Creeping forward blind is how a robot meets a wall at full speed."""
        d = service.decide(sensors(rl=200, rr=200))
        assert d.action != "forward"
        assert d.behaviour == "avoid"

    def test_the_pulse_shortens_as_the_room_closes(self, service):
        roomy = service.decide(sensors(fl=400, fr=400))
        service._front_history.clear()
        tight = service.decide(sensors(fl=60, fr=60))
        assert tight.ms < roomy.ms, "less room should mean a sooner next decision"

    def test_every_decision_carries_an_auto_stop(self, service):
        """A command with no ms leaves the wheels turning if the next packet is lost."""
        for reading in (sensors(fl=300, fr=300), sensors(fl=10, fr=10, rl=80, rr=80),
                        sensors(fl=30, fr=90), sensors()):
            assert service.decide(reading).ms > 0

    def test_speed_is_never_above_what_the_firmware_accepts(self, service):
        assert 0 < service.decide(sensors(fl=300, fr=300)).speed <= 255


class TestItNoticesBeingStuck:
    def test_unchanging_distances_while_driving_mean_wedged(self, service):
        """The ultrasonics cannot see a chair leg between them. Stillness can."""
        seen = [service.decide(sensors(fl=100, fr=100)).behaviour
                for _ in range(WEDGE_MIN_MOVES + WEDGE_WINDOW)]
        assert "unwedge" in seen, seen
        assert seen.index("unwedge") >= WEDGE_MIN_MOVES, (
            "it must have genuinely been trying to move first", seen)

    def test_it_does_not_unwedge_over_and_over(self, service):
        """One escape attempt, then it earns the right to try again."""
        seen = [service.decide(sensors(fl=100, fr=100)).behaviour
                for _ in range(WEDGE_MIN_MOVES + WEDGE_WINDOW)]
        assert seen.count("unwedge") == 1, seen

    def test_a_room_that_is_changing_is_not_wedged(self, service):
        seen = [service.decide(sensors(fl=100 + i * 6, fr=100 + i * 6)).behaviour
                for i in range(WEDGE_MIN_MOVES + WEDGE_WINDOW)]
        assert "unwedge" not in seen, seen

    def test_it_does_not_cry_wedged_before_it_has_even_moved(self, service):
        seen = [service.decide(sensors(fl=100, fr=100)).behaviour
                for _ in range(WEDGE_MIN_MOVES - 1)]
        assert set(seen) == {"cruise"}, seen


class TestItIsSafeToLeaveRunning:
    def test_off_by_default(self):
        assert settings.ROBOT_ROAM_ENABLED is False, (
            "a robot that starts moving when IRIS launches drives off the desk "
            "while you are still reading the startup log"
        )

    async def test_a_disabled_service_does_nothing_at_all(self, service):
        service.enabled = False
        assert await service.tick() is None

    async def test_losing_the_sensors_parks_it(self, service, monkeypatch):
        async def blind(*_a, **_k):
            raise RuntimeError("no answer")

        monkeypatch.setattr("iris.app.services.roam.device_request", blind)
        await service.set_enabled(True)

        for _ in range(FAILURES_BEFORE_PARK - 1):
            assert await service.tick() == "sensor_miss"
            assert service.enabled, "one missed read is not a reason to give up"

        assert await service.tick() == "blind"
        assert service.enabled is False
        assert "sensors" in service.status()["stopped_reason"]

    async def test_one_missed_read_is_forgiven(self, service, monkeypatch):
        calls = {"n": 0}

        async def flaky(device, path, params=None, **_k):
            if path == "/sensors":
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("dropped packet")
                return sensors(fl=300, fr=300)
            return {}

        monkeypatch.setattr("iris.app.services.roam.device_request", flaky)
        await service.set_enabled(True)
        assert await service.tick() == "sensor_miss"
        assert await service.tick() == "cruise"
        assert service.enabled

    async def test_stop_from_the_user_reaches_it_in_one_tick(self, service, monkeypatch):
        from iris.app.tools.devices import navigate

        async def ok(device, path, params=None, **_k):
            return sensors(fl=300, fr=300) if path == "/sensors" else {}

        monkeypatch.setattr("iris.app.services.roam.device_request", ok)
        await service.set_enabled(True)
        navigate.request_stop()
        try:
            assert await service.tick() == "stopped"
            assert service.enabled is False
        finally:
            navigate._Abort.flag = False

    async def test_it_parks_itself_after_the_time_limit(self, service, monkeypatch):
        async def ok(device, path, params=None, **_k):
            return sensors(fl=300, fr=300) if path == "/sensors" else {}

        monkeypatch.setattr("iris.app.services.roam.device_request", ok)
        monkeypatch.setattr(settings, "ROBOT_ROAM_MAX_MINUTES", 1.0)
        await service.set_enabled(True)
        assert await service.tick() == "cruise"

        service._clock_list[0] += 61
        assert await service.tick() == "time_limit"
        assert service.enabled is False

    async def test_turning_it_off_stops_the_wheels(self, service, monkeypatch):
        sent = []

        async def record(device, path, params=None, **_k):
            sent.append((path, dict(params or {})))
            return sensors(fl=300, fr=300) if path == "/sensors" else {}

        monkeypatch.setattr("iris.app.services.roam.device_request", record)
        await service.set_enabled(True)
        await service.tick()
        sent.clear()
        await service.set_enabled(False)
        assert ("/motor", {"dir": "stop"}) in sent

    async def test_it_will_not_start_without_eyes(self, service):
        """Driving blind is the one thing it must never agree to."""
        from iris.app.tools.devices.roam import RobotRoamTool
        from iris.app.tools.base import ToolError

        service._registry.remove("face")
        tool = RobotRoamTool(service=service)
        result = await tool.execute(action="start")
        assert result.success is False
        assert "sensor" in (result.error or "").lower() or "see" in (result.error or "").lower()
        assert service.enabled is False
