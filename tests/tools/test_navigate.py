"""The robot going places on its own: turns, distances, "go back to the board",
stopping for obstacles, stopping for the user, calibration, and doing it later."""

from __future__ import annotations

import pytest

from iris.app.nlu.engine import IntentEngine
from iris.app.tools.base import ToolError
from iris.app.tools.devices import esp32 as esp32_mod
from iris.app.tools.devices import navigate as nav_mod
from iris.app.tools.devices.navigate import RobotNavigateTool, fmt_cm, nearest_distance
from iris.app.tools.devices.registry import Device, DeviceRegistry
from iris.app.tools.automation import reminders as rem_mod
from iris.app.tools.automation.reminders import ScheduleCommandTool


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture()
def registry(tmp_path):
    return DeviceRegistry(path=tmp_path / "devices.json")


@pytest.fixture()
def world(registry, tmp_path, monkeypatch):
    """A robot, a sense board, a fake wire and a clock that only moves when
    the tool sleeps — so a 6-second leg runs in no time."""
    registry.add(Device(name="robot", base_url="http://192.168.1.60", kind="motor"))
    registry.add(Device(name="face", base_url="http://192.168.1.34", kind="face"))
    state = {"motor": [], "readings": [], "clock": FakeClock(), "default_cm": 300}

    async def fake_request(device, path, params=None, hub=None):
        if device.kind == "motor":
            state["motor"].append((path, dict(params or {})))
            return {"ok": True}
        if path == "/sensors":
            cm = state["readings"].pop(0) if state["readings"] else state["default_cm"]
            if isinstance(cm, Exception):
                raise cm
            return {"distances": {"front_left": cm, "front_right": cm + 5, "rear_left": 150, "rear_right": 140}}
        raise AssertionError(path)

    async def fake_sleep(seconds):
        state["clock"].t += seconds

    monkeypatch.setattr(nav_mod, "device_request", fake_request)
    monkeypatch.setattr(RobotNavigateTool, "_sleep", staticmethod(fake_sleep))
    monkeypatch.setattr(RobotNavigateTool, "_clock", staticmethod(state["clock"]))
    monkeypatch.setattr(nav_mod, "calibration_path", lambda: tmp_path / "cal.json")
    monkeypatch.setattr("iris.app.core.config.settings.ROBOT_CM_PER_S", 50.0)
    monkeypatch.setattr("iris.app.core.config.settings.ROBOT_DEG_PER_S", 90.0)
    nav_mod._Abort.flag = False
    state["registry"] = registry
    return state


def moves(state):
    return [(p["dir"], p.get("ms")) for path, p in state["motor"] if path == "/motor"]


# ------------------------------------------------------------------ helpers
def test_distances_read_and_spoken():
    assert nearest_distance({"distances": {"front_left": 80, "front_right": 40, "rear_left": 10}}, "forward") == 40
    assert nearest_distance({"distances": {"front_left": 80, "rear_left": 10}}, "backward") == 10
    assert nearest_distance({"distance_cm": 55}, "forward") == 55
    assert nearest_distance({}, "forward") is None
    assert fmt_cm(45) == "45 centimetres"
    assert fmt_cm(200) == "2 metres"
    assert fmt_cm(140) == "1.4 metres"
    assert fmt_cm(100) == "1 metre"


# --------------------------------------------------------------------- legs
class TestLegs:
    async def test_a_u_turn_is_a_timed_spin(self, world):
        res = await RobotNavigateTool(world["registry"]).execute(preset="u_turn")
        assert res.success, res.error
        assert moves(world) == [("right", 2000)]          # 180 / 90 deg/s
        assert res.result["speech"] == "Done: turned around."

    async def test_a_left_turn_by_degrees(self, world):
        res = await RobotNavigateTool(world["registry"]).execute(turn_degrees=-90)
        assert moves(world) == [("left", 1000)]
        assert "turned left 90 degrees" in res.result["speech"]

    async def test_a_clear_run_drives_the_whole_distance(self, world):
        res = await RobotNavigateTool(world["registry"]).execute(distance_cm=200)
        assert res.success, res.error
        assert moves(world)[0] == ("forward", 4000)        # 200 cm / 50 cm/s
        assert res.result["speech"] == "Done: drove 2 metres."
        assert res.result["completed"] is True
        assert ("stop", None) not in moves(world)         # the firmware's own ms did it

    async def test_an_obstacle_stops_the_leg_early(self, world):
        world["readings"] = [200, 120, 60, 30]
        res = await RobotNavigateTool(world["registry"]).execute(distance_cm=300)
        assert res.success, res.error
        assert moves(world)[-1] == ("stop", None)
        leg = res.result["legs"][0]
        assert leg["blocked_cm"] == 30 and leg["travelled_cm"] < 300
        assert "stopped — something was 30 centimetres ahead" in res.result["speech"]
        assert res.result["completed"] is False

    async def test_reversing_watches_the_rear_sensors(self, world):
        world["default_cm"] = 20   # something right in front — irrelevant when reversing
        res = await RobotNavigateTool(world["registry"]).execute(distance_cm=100, direction="backward")
        assert moves(world)[0][0] == "backward"
        assert res.result["legs"][0]["blocked_cm"] is None   # rear is clear at 140

    async def test_come_back_turns_then_drives_until_the_target(self, world):
        world["readings"] = [180, 120, 70, 33, 20]
        res = await RobotNavigateTool(world["registry"]).execute(preset="come_back", target="the board")
        assert res.success, res.error
        dirs = [d for d, _ in moves(world)]
        assert dirs == ["right", "forward", "stop"]
        assert res.result["speech"] == "Done: turned around, then drove until the board was 33 centimetres ahead."
        assert res.result["completed"] is True

    async def test_until_obstacle_gives_up_after_the_max_leg(self, world, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.ROBOT_MAX_LEG_S", 2.0)
        res = await RobotNavigateTool(world["registry"]).execute(until_obstacle=True, target="the wall")
        assert "without finding the wall, so I stopped" in res.result["speech"]
        assert moves(world)[-1] == ("stop", None)

    async def test_until_obstacle_needs_the_sense_board(self, world):
        world["registry"].remove("face")
        res = await RobotNavigateTool(world["registry"]).execute(preset="come_back")
        assert not res.success and "sense board" in res.error
        # it turned first, then refused the blind leg — and the wheels are not left running
        assert moves(world)[0][0] == "right"

    async def test_moving_without_sensors_is_allowed_but_said(self, world):
        world["registry"].remove("face")
        res = await RobotNavigateTool(world["registry"]).execute(distance_cm=100)
        assert res.success and "without distance sensors" in res.result["speech"]

    async def test_lost_sensors_stop_the_robot(self, world):
        world["readings"] = [ToolError("dead"), ToolError("dead"), ToolError("dead")]
        res = await RobotNavigateTool(world["registry"]).execute(distance_cm=400)
        assert moves(world)[-1] == ("stop", None)
        assert "lost my distance sensors" in res.result["speech"]

    async def test_stop_from_the_user_aborts_the_plan(self, world, monkeypatch):
        """'robot stop' through the motor tool must end a plan in flight."""
        motor = esp32_mod.DeviceMotorTool(world["registry"])

        async def fake_motor_request(device, path, params=None, hub=None):
            world["motor"].append((path, dict(params or {})))
            return {"ok": True}
        monkeypatch.setattr(esp32_mod, "device_request", fake_motor_request)

        async def stop_soon(seconds):
            world["clock"].t += seconds
            if world["clock"].t > 100.5:          # half a second in, the user says stop
                await motor._run(action="stop")
        monkeypatch.setattr(RobotNavigateTool, "_sleep", staticmethod(stop_soon))

        res = await RobotNavigateTool(world["registry"]).execute(
            steps=[{"kind": "move", "distance_cm": 500}, {"kind": "turn", "degrees": 180}]
        )
        assert res.success, res.error
        assert res.result["legs"][0]["aborted"] is True
        assert all(leg.get("aborted") for leg in res.result["legs"])
        assert "stopped when you told me to" in res.result["speech"]
        assert res.result["completed"] is False

    async def test_no_robot_registered_says_how_to_add_one(self, registry, monkeypatch):
        res = await RobotNavigateTool(registry).execute(preset="u_turn")
        assert not res.success and "add device robot" in res.error

    async def test_nothing_to_do_is_refused(self, world):
        res = await RobotNavigateTool(world["registry"]).execute()
        assert not res.success

    async def test_an_explicit_plan_runs_in_order(self, world):
        res = await RobotNavigateTool(world["registry"]).execute(steps=[
            {"kind": "move", "distance_cm": 100}, {"kind": "turn", "degrees": -90},
            {"kind": "wait", "seconds": 1}, {"kind": "move", "distance_cm": 50, "direction": "backward"},
        ])
        assert [d for d, _ in moves(world)] == ["forward", "left", "backward"]
        assert res.result["speech"] == "Done: drove 1 metre, then turned left 90 degrees, then waited 1 seconds, then drove 50 centimetres."


class TestCalibration:
    async def test_calibration_is_saved_and_used(self, world, tmp_path):
        tool = RobotNavigateTool(world["registry"])
        res = await tool.execute(calibrate_cm_per_s=25)
        assert res.success and "25 centimetres a second" in res.result["speech"]
        assert (tmp_path / "cal.json").exists()
        world["motor"].clear()
        await tool.execute(distance_cm=100)
        assert moves(world)[0] == ("forward", 4000)       # 100 / 25 cm/s


# ------------------------------------------------------------- doing it later
class TestScheduleCommand:
    async def test_in_ten_minutes_stores_a_command(self, monkeypatch):
        added = {}

        async def fake_add(**kwargs):
            added.update(kwargs); return {"id": "rem_1", **{k: str(v) for k, v in kwargs.items()}}
        monkeypatch.setattr(rem_mod.default_scheduler_service, "add", fake_add)
        res = await ScheduleCommandTool().execute(command="take a U-turn and go back to the board", in_seconds=600)
        assert res.success, res.error
        assert added["kind"] == "command" and added["text"] == "take a U-turn and go back to the board"
        assert res.result["speech"] == "Okay — in 10 minutes I'll take a U-turn and go back to the board."

    async def test_without_a_time_it_asks(self):
        res = await ScheduleCommandTool().execute(command="robot forward")
        assert not res.success

    async def test_a_due_command_goes_through_the_kernel(self, monkeypatch):
        from iris.app.services.scheduler import SchedulerService
        ran, spoken = [], []

        class FakeKernel:
            async def process_request(self, text, **kw):
                ran.append(text)
                return type("R", (), {"speech": "Done: turned around.", "response": "Done: turned around."})()

        class FakeVoice:
            async def speak(self, text, **kw):
                spoken.append(text)
        monkeypatch.setattr("iris.app.agent.kernel.default_kernel", FakeKernel())
        monkeypatch.setattr("iris.app.voice.service.default_voice_service", FakeVoice())
        await SchedulerService()._announce({"kind": "command", "text": "take a u-turn", "id": "rem_9"})
        assert ran == ["take a u-turn"] and spoken == ["Done: turned around."]


# -------------------------------------------------------------------- phrases
NAV_CASES = [
    ("take a u-turn", "robot_navigate", {"preset": "u_turn"}),
    ("robot, take a U turn", "robot_navigate", {"preset": "u_turn"}),
    ("turn around", "robot_navigate", {"preset": "u_turn"}),
    ("peeche mudo", "robot_navigate", {"preset": "u_turn"}),
    ("take a u-turn and go back to the board", "robot_navigate", {"preset": "come_back", "target": "the board"}),
    ("turn around and come back", "robot_navigate", {"preset": "come_back", "target": "where I started"}),
    ("go back to the board", "robot_navigate", {"preset": "come_back", "target": "the board"}),
    ("come back to the table", "robot_navigate", {"preset": "come_back", "target": "the table"}),
    ("return to the judges", "robot_navigate", {"preset": "come_back", "target": "the judges"}),
    ("go forward 2 metres", "robot_navigate", {"distance_cm": 200.0, "direction": "forward"}),
    ("go 10 m", "robot_navigate", {"distance_cm": 1000.0, "direction": "forward"}),
    ("move back 50 cm", "robot_navigate", {"distance_cm": 50.0, "direction": "backward"}),
    ("robot go 3 feet forward", "robot_navigate", {"distance_cm": 91.4, "direction": "forward"}),
    ("aage 1 meter jao", "robot_navigate", {"distance_cm": 100.0, "direction": "forward"}),
    ("turn left 90 degrees", "robot_navigate", {"turn_degrees": -90.0}),
    ("turn right 45", "robot_navigate", {"turn_degrees": 45.0}),
    ("drive until you reach the wall", "robot_navigate", {"preset": "to_obstacle", "target": "the wall"}),
    ("go to the board", "robot_navigate", {"preset": "to_obstacle", "target": "the board"}),
    ("one metre takes 4 seconds", "robot_navigate", {"calibrate_cm_per_s": 25.0}),
    ("a u-turn takes 2 seconds", "robot_navigate", {"calibrate_deg_per_s": 90.0}),
    ("in 10 minutes take a u-turn and go back to the board", "schedule_command",
     {"command": "take a u-turn and go back to the board", "in_seconds": 600}),
    ("go back to the board in 10 minutes", "schedule_command", {"command": "go back to the board", "in_seconds": 600}),
    ("after 30 seconds robot forward", "schedule_command", {"command": "robot forward", "in_seconds": 30}),
]

UNCHANGED = [
    ("robot forward", "device_motor"), ("robot go back", "device_motor"), ("robot turn left", "device_motor"),
    ("stop the robot", "device_motor"), ("remind me in 10 minutes to stretch", "set_reminder"),
    ("set a timer for 10 minutes", "set_timer"), ("how far is the object", "device_sensors"),
]


class TestPhrases:
    engine = IntentEngine()

    @pytest.mark.parametrize("text,tool,args", NAV_CASES)
    def test_navigation_phrases(self, text, tool, args):
        match = self.engine.match(text)
        assert match is not None and match.tool_name == tool, (text, match)
        for key, value in args.items():
            got = match.arguments.get(key)
            if isinstance(value, float):
                assert got == pytest.approx(value, abs=0.1), (text, match.arguments)
            else:
                assert got == value, (text, match.arguments)

    @pytest.mark.parametrize("text,tool", UNCHANGED)
    def test_old_phrases_keep_their_tools(self, text, tool):
        match = self.engine.match(text)
        assert match is not None and match.tool_name == tool, (text, match)
