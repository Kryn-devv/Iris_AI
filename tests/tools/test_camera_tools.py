"""The robot's eye as IRIS tools: routing, and the tools with a fake camera.

Everything runs with no vision library and no camera. The recogniser, the
frame and the vision model are faked at the seams the tools already expose.
"""

from __future__ import annotations

import httpx
import pytest

from iris.app.nlu.engine import IntentEngine
from iris.app.tools.devices import camera as camera_mod
from iris.app.tools.devices.camera import (
    CameraForgetFaceTool,
    CameraKnownFacesTool,
    CameraLookTool,
    CameraPresenceTool,
    CameraRememberFaceTool,
    CameraWhoTool,
)
from iris.app.tools.devices.registry import Device, DeviceRegistry
from iris.app.vision.robot_eye import FaceStore, Sighting
from iris.app.vision.robot_eye.faces import Box, Match
from iris.app.vision.robot_eye.recognize import SeenFace
from tests.vision.conftest import DETECT_ONLY_SPEC, FakeRecognizer

JPEG = b"\xff\xd8" + b"x" * 64 + b"\xff\xd9"


# ------------------------------------------------------------------ routing
CAMERA_CASES = [
    ("start watching", "camera_watch", {"action": "on"}),
    ("stop watching the camera", "camera_watch", {"action": "off"}),
    ("are you watching", "camera_watch", {"action": "status"}),
    ("who am I", "camera_who", {}),
    ("Who is that?", "camera_who", {}),
    ("who is in front of you", "camera_who", {}),
    ("do you recognise me", "camera_who", {}),
    ("can you see me", "camera_who", {}),
    ("main kaun hoon", "camera_who", {}),
    ("hey iris, who am I please", "camera_who", {}),
    ("what do you see", "camera_look", {"kind": "scene"}),
    ("what's in front of you", "camera_look", {"kind": "scene"}),
    ("kya dikh raha hai", "camera_look", {"kind": "scene"}),
    ("what is this", "camera_look", {"kind": "object"}),
    ("what am I holding", "camera_look", {"kind": "object"}),
    ("ye kya hai", "camera_look", {"kind": "object"}),
    ("is this apple ripe", "camera_look", {"kind": "ripeness"}),
    ("read this label", "camera_look", {"kind": "text"}),
    ("what does this say", "camera_look", {"kind": "text"}),
    ("how many things can you see", "camera_look", {"kind": "count"}),
    ("remember my face as Prakash", "camera_remember_face", {"name": "Prakash"}),
    ("remember my face as prakash sahu", "camera_remember_face", {"name": "Prakash Sahu"}),
    ("remember me as Payal", "camera_remember_face", {"name": "Payal"}),
    ("forget my face", "camera_forget_face", {}),
    ("forget Aditi's face", "camera_forget_face", {"name": "Aditi"}),
    ("forget every face you know", "camera_forget_face", {"everyone": True}),
    ("who do you know", "camera_known_faces", {}),
    ("kisko pehchante ho", "camera_known_faces", {}),
    ("can you see anyone", "camera_presence", {}),
    ("is anyone in front of you", "camera_presence", {}),
    ("has anything moved", "camera_presence", {}),
    ("koi samne hai kya", "camera_presence", {}),
    ("add device eye at 192.168.43.42 as camera", "register_device", {"name": "eye", "kind": "camera"}),
]

#: Phrasings that already had a good answer and must keep it.
REGRESSION_CASES = [
    ("is there any motion", "device_sensors"),      # the PIR sees the whole room
    ("koi hai kya", "device_sensors"),
    ("what's the temperature", "device_sensors"),
    ("look at me", "face_emotion"),                 # the OLED eyes turn
    ("look happy", "face_emotion"),
    ("who is Alan Turing", "wikipedia"),            # a real encyclopaedia question
    ("mausam kaisa hai", "weather"),
    ("robot forward", "device_motor"),
]


class TestCameraRouting:
    engine = IntentEngine()

    @pytest.mark.parametrize("text,tool,args", CAMERA_CASES)
    def test_routes_to_the_camera_tool(self, text, tool, args):
        match = self.engine.match(text)
        assert match is not None and match.tool_name == tool, f"{text!r} -> {match}"
        for key, value in args.items():
            assert match.arguments.get(key) == value, f"{text!r} gave {match.arguments}"

    @pytest.mark.parametrize("text,tool", REGRESSION_CASES)
    def test_existing_routing_is_untouched(self, text, tool):
        match = self.engine.match(text)
        assert match is not None and match.tool_name == tool, f"{text!r} -> {match}"

    def test_camera_who_comes_before_who_is(self):
        """Otherwise "who is that" is a Wikipedia lookup, not a look."""
        order = [r.name for r in self.engine.rules]
        assert order.index("camera_who") < order.index("who_is")

    def test_every_camera_rule_is_reachable(self):
        camera_rules = {r.name for r in self.engine.rules if r.name.startswith("camera_")}
        reached = {self.engine.match(t).rule_name for t, _, _ in CAMERA_CASES if self.engine.match(t)}
        assert camera_rules - reached == set()


# -------------------------------------------------------------------- tools
@pytest.fixture()
def registry(tmp_path):
    return DeviceRegistry(path=tmp_path / "devices.json")


@pytest.fixture()
def eye(registry):
    return registry.add(Device(name="eye", base_url="http://192.168.43.42", kind="camera"))


@pytest.fixture()
def store(tmp_path):
    return FaceStore(tmp_path / "faces.json")


def _seen(name=None, box=Box(300, 200, 200, 200), width=1024, height=768):
    match = Match(name=name, distance=0.3, confidence=0.7, threshold=0.55, metric="euclidean") if name else None
    face = SeenFace(box=box, fraction=(box.w * box.h) / (width * height), match=match)
    return Sighting(faces=(face,), frame_width=width, frame_height=height, backend="fake", primary=face)


class TestNoCamera:
    async def test_every_tool_explains_how_to_add_one(self, registry):
        for tool in (CameraWhoTool(registry), CameraPresenceTool(registry),
                     CameraRememberFaceTool(registry)):
            res = await tool.execute(**({"name": "Prakash"} if tool.name == "camera_remember_face" else {}))
            assert not res.success and "add device eye" in res.error, tool.name

    async def test_a_non_camera_device_is_refused_by_name(self, registry):
        registry.add(Device(name="robot", base_url="http://192.168.43.60", kind="motor"))
        res = await CameraPresenceTool(registry).execute(device="robot")
        assert not res.success and "not a camera" in res.error


class TestPresence:
    async def test_movement_is_reported_with_a_direction(self, registry, eye, monkeypatch):
        async def fake_json(url, params=None, timeout=None):
            assert url.endswith("/motion")
            return {"enabled": True, "ready": True, "moved": True, "recent": True,
                    "percent": 12, "since_motion_ms": 0, "look": {"x": 60, "y": 0}}
        monkeypatch.setattr(camera_mod, "lan_get", fake_json)
        turned = []
        async def fake_device_request(device, path, params=None, hub=None):
            turned.append((device.name, path, params)); return {"ok": True}
        monkeypatch.setattr(camera_mod, "device_request", fake_device_request)
        registry.add(Device(name="face", base_url="http://192.168.43.34", kind="face"))

        res = await CameraPresenceTool(registry).execute()
        assert res.success
        assert res.result["speech"] == "Yes, something's moving to my right."
        assert res.result["look"] == {"x": 60, "y": 0}
        # the OLED eyes were pointed at it
        assert turned == [("face", "/look", {"x": 60, "y": 0})] and res.result["eyes_turned"]

    async def test_an_older_firmware_without_motion_says_so(self, registry, eye, monkeypatch):
        async def fake_json(url, params=None, timeout=None):
            return {"firmware": "old"}
        monkeypatch.setattr(camera_mod, "lan_get", fake_json)
        res = await CameraPresenceTool(registry).execute()
        assert res.success and "doesn't watch for movement" in res.result["speech"]


class TestWho:
    async def test_the_owner_is_you(self, registry, eye, store, monkeypatch):
        async def fake_bytes(url, params=None):
            assert url.endswith("/capture") and params["size"] == "xga"
            return JPEG
        monkeypatch.setattr(camera_mod, "_lan_get_bytes", fake_bytes)
        recognizer = FakeRecognizer(faces=[])
        monkeypatch.setattr(CameraWhoTool, "_recognizer", lambda self, need_embeddings=True: recognizer)
        store.enroll("Prakash", (1.0, 0.0, 0.0, 0.0), backend="fake", metric="euclidean", now=1.0, owner=True)
        monkeypatch.setattr(camera_mod, "look", lambda frame, rec, st, **kw: _seen("Prakash"))
        turned = []
        async def fake_device_request(device, path, params=None, hub=None):
            turned.append((path, params)); return {"ok": True}
        monkeypatch.setattr(camera_mod, "device_request", fake_device_request)
        registry.add(Device(name="face", base_url="http://192.168.43.34", kind="face"))

        res = await CameraWhoTool(registry, store=store).execute()
        assert res.success, res.error
        assert res.result["speech"] == "Yes, that's you."
        assert res.result["faces"][0]["name"] == "Prakash"
        # face centred at (400, 300) in a 1024x768 frame -> a little left and up
        assert res.result["look"] == {"x": -22, "y": -22}
        assert turned == [("/look", {"x": -22, "y": -22})]

    async def test_a_detect_only_backend_still_answers(self, registry, eye, store, monkeypatch):
        async def fake_bytes(url, params=None):
            return JPEG
        monkeypatch.setattr(camera_mod, "_lan_get_bytes", fake_bytes)
        recognizer = FakeRecognizer(faces=[], spec=DETECT_ONLY_SPEC)
        monkeypatch.setattr(CameraWhoTool, "_recognizer", lambda self, need_embeddings=True: recognizer)
        monkeypatch.setattr(camera_mod, "look", lambda frame, rec, st, **kw: _seen(None))
        res = await CameraWhoTool(registry, store=store).execute(turn_eyes=False)
        assert res.success and "can't tell whose it is" in res.result["speech"]
        assert res.result["can_identify"] is False

    async def test_a_message_instead_of_a_frame_is_named(self, registry, eye, store, monkeypatch):
        async def fake_bytes(url, params=None):
            return b'{"error":"camera init failed"}'
        monkeypatch.setattr(camera_mod, "_lan_get_bytes", fake_bytes)
        monkeypatch.setattr(CameraWhoTool, "_recognizer", lambda self, need_embeddings=True: FakeRecognizer(faces=[]))
        res = await CameraWhoTool(registry, store=store).execute()
        assert not res.success and "not a frame" in res.error

    async def test_nothing_installed_says_what_to_install(self, registry, eye, store, monkeypatch):
        monkeypatch.setattr(camera_mod, "module_available", lambda name: False)
        res = await CameraWhoTool(registry, store=store).execute()
        assert not res.success and "pip install" in res.error


class TestRememberAndForget:
    async def test_first_person_becomes_the_owner(self, registry, eye, store, monkeypatch):
        async def fake_bytes(url, params=None):
            return JPEG
        monkeypatch.setattr(camera_mod, "_lan_get_bytes", fake_bytes)
        recognizer = FakeRecognizer(faces=[(Box(300, 200, 300, 300), (1.0, 0.0, 0.0, 0.0))])
        monkeypatch.setattr(CameraRememberFaceTool, "_recognizer", lambda self, need_embeddings=True: recognizer)
        # ``enroll`` binds the real JPEG decoder as a default argument, so it is
        # injected here instead of patched: the fake bytes are not a JPEG.
        real_enroll = camera_mod.enroll_face
        fake_image = type("F", (), {"shape": (768, 1024, 3)})()
        monkeypatch.setattr(
            camera_mod, "enroll_face",
            lambda frame, name, rec, st, **kw: real_enroll(
                frame, name, rec, st, decode=lambda data: fake_image, **kw),
        )

        res = await CameraRememberFaceTool(registry, store=store).execute(name="Prakash")
        assert res.success, res.error
        assert res.result["owner"] is True and res.result["samples"] == 1
        assert store.owner().name == "Prakash"

        listed = await CameraKnownFacesTool(registry, store=store).execute()
        assert listed.result["speech"] == "I know one face: Prakash (you)."

        gone = await CameraForgetFaceTool(registry, store=store).execute(name="Prakash")
        assert gone.success and gone.result["forgotten"] == 1 and len(store) == 0

    async def test_forgetting_everyone_counts(self, registry, store):
        store.enroll("A", (1.0, 0.0), backend="fake", metric="euclidean", now=1.0)
        store.enroll("B", (0.0, 1.0), backend="fake", metric="euclidean", now=1.0)
        res = await CameraForgetFaceTool(registry, store=store).execute(everyone=True)
        assert res.success and res.result["forgotten"] == 2


class TestLook:
    async def test_without_a_vision_model_it_names_the_setting(self, registry, eye, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.VISION_MODEL", None)
        res = await CameraLookTool(registry).execute(kind="object")
        assert not res.success and "VISION_MODEL" in res.error

    async def test_the_frame_reaches_the_model_as_an_image(self, registry, eye, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.VISION_MODEL", "some/vision-model")
        async def fake_bytes(url, params=None):
            return JPEG
        monkeypatch.setattr(camera_mod, "_lan_get_bytes", fake_bytes)

        class FakeGateway:
            calls = []
            async def generate(self, prompt, **kwargs):
                self.calls.append(kwargs)
                return type("R", (), {"content": "That is a red apple. It looks ripe. Enjoy it.", "model_name": "some/vision-model"})()

        gw = FakeGateway()
        res = await CameraLookTool(registry, gateway=gw).execute(kind="object")
        assert res.success, res.error
        assert res.result["speech"] == "That is a red apple. It looks ripe."     # two spoken sentences
        assert res.result["display"].endswith("Enjoy it.")
        sent = gw.calls[0]
        assert sent["capability"] == "VISION" and sent["model"] == "some/vision-model"
        user = [m for m in sent["messages"] if m["role"] == "user"][0]
        assert user["content"][1]["type"] == "image_url"
        assert user["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
