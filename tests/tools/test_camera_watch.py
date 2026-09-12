"""The camera acting on its own: greet, notice strangers, name objects — once.

Everything is faked at the wire: the camera's /motion JSON and /capture bytes,
the recogniser, the clock, the voice. No network, no optional packages.
"""

from __future__ import annotations

import pytest

from iris.app.core.bus import EventBus, Topics
from iris.app.nlu.engine import IntentEngine
from iris.app.nlu.rules import RULES
from iris.app.services import camera_watch as watch_mod
from iris.app.services.camera_watch import CameraWatchService, greeting_for
from iris.app.tools.devices import camera as camera_mod
from iris.app.tools.devices.camera import CameraWatchTool
from iris.app.tools.devices.registry import Device, DeviceRegistry
from iris.app.vision.robot_eye import Box, FaceStore, Match, Sighting
from iris.app.vision.robot_eye.recognize import SeenFace
from tests.vision.conftest import DETECT_ONLY_SPEC, FakeRecognizer

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


# ---------------------------------------------------------------- fixtures
class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _match(name):
    return Match(name=name, distance=0.2, confidence=0.9, threshold=0.6, metric="euclidean")


def _sighting(*names, unknown=0, tiny_unknown=0):
    faces = []
    for name in names:
        faces.append(SeenFace(box=Box(300, 200, 200, 200), fraction=0.05, match=_match(name)))
    for _ in range(unknown):
        faces.append(SeenFace(box=Box(600, 200, 200, 200), fraction=0.05, match=None))
    for _ in range(tiny_unknown):
        faces.append(SeenFace(box=Box(10, 10, 8, 8), fraction=0.0001, match=None, too_small=True))
    return Sighting(faces=tuple(faces), frame_width=1024, frame_height=768, backend="fake",
                    primary=faces[0] if faces else None)


@pytest.fixture
def registry(tmp_path):
    return DeviceRegistry(path=tmp_path / "devices.json")


@pytest.fixture
def world(registry, tmp_path, monkeypatch):
    """A camera, a face board, a fake wire and a service wired to fakes."""
    registry.add(Device(name="eye", base_url="http://192.168.43.42", kind="camera"))
    registry.add(Device(name="face", base_url="http://192.168.43.34", kind="face"))
    store = FaceStore(tmp_path / "faces.json")
    store.enroll("Prakash", (1.0, 0.0, 0.0, 0.0), backend="fake", metric="euclidean", now=1.0, owner=True)
    store.enroll("Aditi", (0.0, 1.0, 0.0, 0.0), backend="fake", metric="euclidean", now=1.0)

    state = {"motion": {"enabled": True, "ready": True, "moved": True, "recent": True,
                        "percent": 12, "since_motion_ms": 0, "look": {"x": 20, "y": 0}},
             "frames": 0, "spoken": [], "faces": [], "sighting": _sighting("Prakash"),
             "vision": [], "answer": "I see a red apple."}

    async def fake_json(url, params=None, timeout=None):
        assert url.endswith("/motion"), url
        return dict(state["motion"])

    async def fake_bytes(url, params=None):
        assert url.endswith("/capture"), url
        state["frames"] += 1
        return JPEG

    async def fake_device_request(device, path, params=None, hub=None):
        state["faces"].append((device.name, path, dict(params or {})))
        return {"ok": True}

    async def speak(sentence):
        state["spoken"].append(sentence)

    class Gateway:
        async def generate(self, prompt, **kwargs):
            state["vision"].append(kwargs)
            return type("R", (), {"content": state["answer"], "model_name": "vision"})()

    monkeypatch.setattr(camera_mod, "lan_get", fake_json)
    monkeypatch.setattr(camera_mod, "_lan_get_bytes", fake_bytes)
    monkeypatch.setattr("iris.app.tools.devices.face.device_request", fake_device_request)
    monkeypatch.setattr(watch_mod, "look", lambda frame, rec, st, **kw: state["sighting"])
    monkeypatch.setattr("iris.app.core.config.settings.VISION_MODEL", "some/vision-model")
    monkeypatch.setattr("iris.app.core.config.settings.CAMERA_WATCH_ENABLED", True)

    clock = Clock()
    bus = EventBus()
    service = CameraWatchService(
        registry, bus, store,
        recognizer_factory=lambda: FakeRecognizer(faces=[]),
        gateway_factory=lambda: Gateway(),
        speak=speak, clock=clock, hour=lambda: 19,
    )
    service.enabled = True
    state["service"] = service
    state["clock"] = clock
    state["bus"] = bus
    state["store"] = store
    return state


# --------------------------------------------------------------- greetings
class TestGreeting:
    def test_the_sentence_follows_the_hour_and_the_owner(self):
        assert greeting_for("Prakash", owner=True, hour=8) == "Good morning, Prakash. Good to see you."
        assert greeting_for("Aditi", owner=False, hour=15) == "Good afternoon, Aditi."
        assert greeting_for("Aditi", owner=False, hour=22) == "Good evening, Aditi."
        assert greeting_for("Aditi", owner=False, hour=2).startswith("You're up late")

    async def test_the_owner_is_greeted_once_and_the_eyes_turn(self, world):
        service = world["service"]
        assert await service.tick() == "greeted"
        assert world["spoken"] == ["Good evening, Prakash. Good to see you."]
        # the face board got one combined push: happy, looking a little left/up
        assert len(world["faces"]) == 1
        name, path, params = world["faces"][0]
        assert name == "face" and path == "/face" and params["emotion"] == "happy"
        assert int(params["look_x"]) == -22 and int(params["look_y"]) == -22

        # still standing there two seconds later: nothing more is said
        world["clock"].t += 3
        assert await service.tick() in ("seen", "cooldown")
        assert len(world["spoken"]) == 1

    async def test_a_second_person_gets_their_own_greeting(self, world):
        service = world["service"]
        await service.tick()
        world["sighting"] = _sighting("Prakash", "Aditi")
        world["clock"].t += 3
        assert await service.tick() == "greeted"
        assert world["spoken"][-1] == "Good evening, Aditi."

    async def test_greeted_again_after_the_cooldown(self, world, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.CAMERA_GREET_COOLDOWN_S", 60.0)
        service = world["service"]
        await service.tick()
        world["clock"].t += 61
        assert await service.tick() == "greeted"
        assert len(world["spoken"]) == 2

    async def test_a_stranger_is_mentioned_once(self, world):
        service = world["service"]
        world["sighting"] = _sighting(unknown=1)
        assert await service.tick() == "stranger"
        assert world["spoken"] == ["Someone I don't recognise is here."]
        assert world["faces"][0][2]["emotion"] == "suspicious"
        world["clock"].t += 3
        assert await service.tick() == "seen"
        assert len(world["spoken"]) == 1

    async def test_a_far_away_face_is_not_called_a_stranger(self, world):
        world["sighting"] = _sighting(tiny_unknown=1)
        assert await world["service"].tick() == "seen"
        assert world["spoken"] == []

    async def test_strangers_can_be_silenced(self, world, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.CAMERA_ANNOUNCE_STRANGERS", False)
        world["sighting"] = _sighting(unknown=1)
        assert await world["service"].tick() == "seen"
        assert world["spoken"] == []

    async def test_sightings_reach_the_bus(self, world):
        sub = world["bus"].subscribe([Topics.VISION_SIGHTING])
        await world["service"].tick()
        events = []
        while not sub.queue.empty():
            events.append(sub.queue.get_nowait().payload["event"])
        assert "greeted" in events


# ------------------------------------------------------------------ objects
class TestObjects:
    async def test_something_set_down_is_named_once(self, world):
        service = world["service"]
        world["sighting"] = _sighting()                  # no face in the frame
        world["motion"].update(moved=False, recent=True)  # it moved, then stopped
        assert await service.tick() == "object"
        assert world["spoken"] == ["I see a red apple."]
        assert world["vision"][0]["capability"] == "VISION"
        assert world["vision"][0]["model"] == "some/vision-model"

        # the same thing is still there a minute later: not repeated
        world["clock"].t += 60
        assert await service.tick() == "nothing"
        assert len(world["spoken"]) == 1

    async def test_a_new_object_is_named(self, world):
        service = world["service"]
        world["sighting"] = _sighting()
        world["motion"].update(moved=False, recent=True)
        await service.tick()
        world["clock"].t += 60
        world["answer"] = "I see a blue mug."
        assert await service.tick() == "object"
        assert world["spoken"][-1] == "I see a blue mug."

    async def test_nothing_means_silence(self, world):
        world["sighting"] = _sighting()
        world["motion"].update(moved=False, recent=True)
        world["answer"] = "NOTHING."
        assert await world["service"].tick() == "nothing"
        assert world["spoken"] == []

    async def test_while_still_moving_no_model_call_is_made(self, world):
        world["sighting"] = _sighting()
        world["motion"].update(moved=True, recent=True)
        assert await world["service"].tick() == "nothing"
        assert world["vision"] == []

    async def test_without_a_vision_model_objects_are_skipped(self, world, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.VISION_MODEL", None)
        world["sighting"] = _sighting()
        world["motion"].update(moved=False, recent=True)
        assert await world["service"].tick() == "nothing"
        assert world["vision"] == []

    async def test_a_slow_model_is_not_an_error(self, world):
        class Broken:
            async def generate(self, *a, **k):
                raise RuntimeError("timeout")
        world["service"]._gateway_factory = lambda: Broken()
        world["sighting"] = _sighting()
        world["motion"].update(moved=False, recent=True)
        assert await world["service"].tick() == "nothing"
        assert world["spoken"] == []


# ------------------------------------------------------------ being cheap
class TestCheapness:
    async def test_nothing_moving_means_no_frame(self, world):
        world["motion"].update(moved=False, recent=False)
        assert await world["service"].tick() == "quiet"
        assert world["frames"] == 0

    async def test_frames_are_rate_limited(self, world):
        service = world["service"]
        await service.tick()
        world["clock"].t += 0.5
        assert await service.tick() == "cooldown"
        assert world["frames"] == 1

    async def test_disabled_means_no_network_at_all(self, world):
        service = world["service"]
        service.set_enabled(False)
        assert await service.tick() is None
        assert world["frames"] == 0 and world["spoken"] == []

    async def test_no_camera_is_a_silent_no_op(self, registry, world):
        registry.remove("eye")
        assert await world["service"].tick() is None

    async def test_a_dead_camera_backs_off(self, world, monkeypatch):
        async def dead(url, params=None, timeout=None):
            from iris.app.tools.base import ToolError
            raise ToolError("Could not reach the camera")
        monkeypatch.setattr(camera_mod, "lan_get", dead)
        service = world["service"]
        for _ in range(watch_mod.FAILURES_BEFORE_BACKOFF):
            assert await service.tick() == "error"
        assert await service.tick() == "paused"
        assert service.status()["paused_for_s"] > 0
        assert service._interval() >= 0.2

    async def test_a_camera_without_motion_detection_is_looked_at_slowly(self, world):
        service = world["service"]
        world["motion"] = {"firmware": "old"}
        assert await service.tick() == "greeted"
        world["clock"].t += 3
        assert await service.tick() == "cooldown"
        world["clock"].t += watch_mod.BLIND_LOOK_INTERVAL_S
        assert await service.tick() in ("seen", "greeted")
        assert world["frames"] == 2

    async def test_no_face_backend_still_watches_for_objects(self, world):
        service = world["service"]
        service._recognizer_factory = lambda: None
        world["motion"].update(moved=False, recent=True)
        assert await service.tick() == "object"
        assert service.status()["can_identify"] is False

    async def test_a_detect_only_backend_notices_strangers(self, world):
        service = world["service"]
        service._recognizer_factory = lambda: FakeRecognizer(faces=[], spec=DETECT_ONLY_SPEC)
        world["sighting"] = _sighting(unknown=1)
        assert await service.tick() == "stranger"

    async def test_the_voice_failing_still_shows_the_sentence(self, world):
        async def broken(sentence):
            raise RuntimeError("no audio device")
        world["service"]._speak_impl = broken
        sub = world["bus"].subscribe([Topics.VOICE_SPEAKING])
        assert await world["service"].tick() == "greeted"
        ev = sub.queue.get_nowait()
        assert ev.payload["text"].startswith("Good evening, Prakash")


# --------------------------------------------------------------- the tool
class TestWatchTool:
    async def test_on_off_status(self, registry, world):
        service = world["service"]
        tool = CameraWatchTool(registry, store=world["store"], service=service)
        off = await tool.execute(action="off")
        assert off.success and service.enabled is False and "stopped watching" in off.result["speech"]
        status = await tool.execute(action="status")
        assert "not watching" in status.result["speech"]
        on = await tool.execute(action="on")
        assert service.enabled is True
        assert on.result["speech"].startswith("I'm watching. I'll greet the people I know")
        assert "name anything placed" in on.result["speech"]
        await service.tick()
        status = await tool.execute(action="status")
        assert "1 greetings" in status.result["speech"] and "Prakash" in status.result["speech"]

    async def test_turning_on_without_a_camera_says_how_to_add_one(self, registry, world):
        registry.remove("eye")
        tool = CameraWatchTool(registry, store=world["store"], service=world["service"])
        res = await tool.execute(action="on")
        assert res.success and "add device eye" in res.result["speech"]

    async def test_on_without_a_vision_model_says_so(self, registry, world, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.VISION_MODEL", None)
        tool = CameraWatchTool(registry, store=world["store"], service=world["service"])
        res = await tool.execute(action="on")
        assert "VISION_MODEL" in res.result["speech"]

    async def test_on_with_a_single_ability_reads_as_a_sentence(self, registry, world, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.VISION_MODEL", None)
        monkeypatch.setattr("iris.app.core.config.settings.CAMERA_ANNOUNCE_STRANGERS", False)
        tool = CameraWatchTool(registry, store=world["store"], service=world["service"])
        res = await tool.execute(action="on")
        assert res.result["speech"].startswith("I'm watching. I'll greet the people I know.")
        assert "  " not in res.result["speech"] and " and ." not in res.result["speech"]

    async def test_a_bad_action_is_refused(self, registry, world):
        tool = CameraWatchTool(registry, store=world["store"], service=world["service"])
        res = await tool.execute(action="maybe")
        assert not res.success


WATCH_CASES = [
    ("start watching", "on"), ("stop watching", "off"), ("stop watching the camera", "off"),
    ("start watching me", "on"), ("start watching the house", "on"), ("start watching for people", "on"),
    ("start greeting people", "on"), ("greet me", "on"), ("keep an eye on the camera", "on"),
    ("keep an eye out", "on"), ("watch the door", "on"), ("are you watching?", "status"),
    ("pause watching", "off"), ("greet me automatically", "on"), ("dekhte raho", "on"),
    ("dekhna band karo", "off"), ("what have you seen so far", "status"),
    ("can you keep watching", "on"), ("please stop watching me", "off"),
]


class TestWatchRouting:
    engine = IntentEngine()

    @pytest.mark.parametrize("text,action", WATCH_CASES)
    def test_phrases_reach_the_watch_tool(self, text, action):
        result = self.engine.match(text)
        assert result is not None and result.tool_name == "camera_watch", (text, result)
        assert result.arguments.get("action") == action

    @pytest.mark.parametrize("text", [
        "watch youtube", "set a stopwatch", "watch a movie", "I want to watch the match",
        "stop", "stop the timer", "who am I",
    ])
    def test_other_watching_is_left_alone(self, text):
        result = self.engine.match(text)
        assert result is None or result.tool_name != "camera_watch", (text, result)

    def test_the_rules_exist_once(self):
        names = [r.name for r in RULES if r.name.startswith("camera_watch")]
        assert sorted(names) == ["camera_watch_off", "camera_watch_on", "camera_watch_status"]


class TestSharedStore:
    """A face learned through the tool must be greeted by the watcher without a
    restart: both must be looking at the same FaceStore."""

    def test_tools_and_watcher_share_one_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(camera_mod, "_shared_store", None)
        monkeypatch.setattr(camera_mod, "faces_path", lambda: tmp_path / "faces.json")
        from iris.app.tools.devices.camera import CameraRememberFaceTool, CameraWhoTool

        remember = CameraRememberFaceTool()
        who = CameraWhoTool()
        watcher = CameraWatchService()
        assert remember.store is who.store is watcher.store
        remember.store.enroll("Prakash", (1.0, 0.0), backend="fake", metric="euclidean", now=1.0, owner=True)
        assert watcher.store.owner().name == "Prakash"
