"""Building camera requests and reading its answers."""

from __future__ import annotations

import pytest

from iris.app.vision.robot_eye.camera import (
    FACE_FRAME_SIZE,
    FRAME_SIZES,
    CameraEye,
    Presence,
    capture_params,
    check_frame,
    looks_like_jpeg,
    parse_motion,
)
from iris.app.vision.robot_eye.errors import NoFrame

JPEG = b"\xff\xd8" + b"payload" + b"\xff\xd9"


class TestCaptureParams:
    def test_no_arguments_sends_nothing(self):
        assert capture_params() == {}

    def test_a_known_size_is_passed_through_lowercased(self):
        assert capture_params(size="SVGA") == {"size": "svga"}

    def test_an_unknown_size_is_caught_here_not_by_the_camera(self):
        with pytest.raises(ValueError, match="not a resolution"):
            capture_params(size="4k")

    def test_the_error_lists_the_sizes_that_do_work(self):
        with pytest.raises(ValueError) as excinfo:
            capture_params(size="4k")
        for name in ("qvga", "svga", "uxga"):
            assert name in str(excinfo.value)

    def test_the_face_default_is_one_the_firmware_accepts(self):
        assert FACE_FRAME_SIZE in FRAME_SIZES
        assert capture_params(size=FACE_FRAME_SIZE)["size"] == FACE_FRAME_SIZE

    def test_warmup_is_bounded(self):
        assert capture_params(warmup=3) == {"warmup": "3"}
        with pytest.raises(ValueError, match="between 0 and 8"):
            capture_params(warmup=9)
        with pytest.raises(ValueError, match="between 0 and 8"):
            capture_params(warmup=-1)

    def test_a_zero_warmup_is_simply_omitted(self):
        assert capture_params(warmup=0) == {}

    def test_flash_can_be_asked_for_plainly(self):
        assert capture_params(flash=True) == {"flash": "1"}

    def test_a_flash_duration_is_bounded_to_what_the_firmware_allows(self):
        assert capture_params(flash=True, flash_ms=200)["flash_ms"] == "200"
        with pytest.raises(ValueError, match="between 10 and 5000"):
            capture_params(flash=True, flash_ms=9)
        with pytest.raises(ValueError, match="between 10 and 5000"):
            capture_params(flash=True, flash_ms=6000)

    def test_a_duration_without_a_flash_is_a_mistake_worth_reporting(self):
        with pytest.raises(ValueError, match="only means something"):
            capture_params(flash_ms=200)

    def test_a_token_travels_with_the_request(self):
        assert capture_params(token="s3cret")["token"] == "s3cret"

    def test_an_empty_token_is_not_sent(self):
        assert "token" not in capture_params(token="")


class TestParseMotion:
    def test_a_full_reply_is_read_completely(self):
        presence = parse_motion({
            "enabled": True, "ready": True, "moved": True, "recent": True,
            "percent": 7, "changed_cells": 54, "since_motion_ms": 120,
            "samples": 900, "events": 4,
            "look": {"x": -35, "y": 12},
            "box": {"x": 100, "y": 200, "w": 300, "h": 400, "units": "per-mille"},
        })
        assert presence.enabled and presence.moved and presence.recent
        assert presence.percent == 7
        assert presence.changed_cells == 54
        assert presence.look == (-35, 12)
        assert presence.box == {"x": 100, "y": 200, "w": 300, "h": 400}
        assert presence.somebody_there

    def test_motion_disabled_carries_the_reason_rather_than_raising(self):
        presence = parse_motion({"enabled": False, "reason": "ENABLE_MOTION is 0 in config.h"})
        assert not presence.enabled
        assert "ENABLE_MOTION" in presence.reason
        assert not presence.somebody_there

    def test_a_still_scene_has_no_box_and_no_look(self):
        presence = parse_motion({"enabled": True, "ready": True, "moved": False,
                                 "box": None, "look": None})
        assert presence.box is None
        assert presence.look is None
        assert not presence.somebody_there

    def test_recent_motion_still_counts_as_somebody_there(self):
        presence = parse_motion({"enabled": True, "moved": False, "recent": True})
        assert presence.somebody_there

    def test_an_empty_reply_parses_to_disabled(self):
        presence = parse_motion({})
        assert not presence.enabled
        assert presence.reason

    def test_a_non_object_reply_does_not_raise(self):
        # Old firmware, or a proxy returning a string.
        assert not parse_motion("not json").enabled          # type: ignore[arg-type]
        assert not parse_motion(None).enabled                # type: ignore[arg-type]

    def test_string_booleans_from_custom_firmware_are_understood(self):
        presence = parse_motion({"enabled": "true", "moved": "1", "recent": "no"})
        assert presence.enabled and presence.moved and not presence.recent

    def test_junk_numbers_fall_back_rather_than_crash(self):
        presence = parse_motion({"enabled": True, "percent": "lots", "samples": None})
        assert presence.percent == 0
        assert presence.samples == 0

    def test_a_malformed_box_is_dropped_not_half_read(self):
        presence = parse_motion({"enabled": True, "moved": True, "box": [1, 2, 3, 4]})
        assert presence.box is None

    def test_a_partial_box_fills_the_gaps_with_zero(self):
        presence = parse_motion({"enabled": True, "moved": True, "box": {"x": 5}})
        assert presence.box == {"x": 5, "y": 0, "w": 0, "h": 0}


class TestFrameChecking:
    def test_a_real_jpeg_passes(self):
        assert looks_like_jpeg(JPEG)
        assert check_frame(JPEG) == JPEG

    def test_an_html_error_page_is_named_as_such(self):
        with pytest.raises(NoFrame, match="a message, not a frame"):
            check_frame(b"<html><body>500 Internal Server Error</body></html>")

    def test_a_json_error_body_is_named_as_such(self):
        with pytest.raises(NoFrame, match="a message, not a frame"):
            check_frame(b'{"error":"camera busy"}')

    def test_arbitrary_bytes_are_reported_as_not_a_jpeg(self):
        with pytest.raises(NoFrame, match="not a JPEG"):
            check_frame(b"\x00\x01\x02\x03\x04\x05")

    def test_a_truncated_frame_is_refused_rather_than_half_decoded(self):
        with pytest.raises(NoFrame, match="incomplete"):
            check_frame(b"\xff\xd8" + b"cut off here")

    def test_an_empty_body_says_how_many_bytes_arrived(self):
        with pytest.raises(NoFrame, match="0 bytes"):
            check_frame(b"")

    def test_non_bytes_input_is_refused(self):
        with pytest.raises(NoFrame, match="did not send image data"):
            check_frame("a string")            # type: ignore[arg-type]


class FakeTransport:
    """Records what was asked for and replies with whatever it was given."""

    def __init__(self, frame: bytes = JPEG, json_reply=None):
        self.frame = frame
        self.json_reply = json_reply if json_reply is not None else {"enabled": True}
        self.byte_calls = []
        self.json_calls = []

    async def fetch_bytes(self, path, params):
        self.byte_calls.append((path, dict(params or {})))
        return self.frame

    async def fetch_json(self, path, params):
        self.json_calls.append((path, dict(params or {})))
        return self.json_reply


class TestCameraEye:
    async def test_capture_hits_the_capture_path(self):
        transport = FakeTransport()
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json)
        frame = await eye.capture()
        assert frame == JPEG
        assert transport.byte_calls[0][0] == "/capture"
        assert eye.last_frame_bytes == len(JPEG)

    async def test_capture_passes_the_options_on(self):
        transport = FakeTransport()
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json, token="tok")
        await eye.capture(size="xga", warmup=2, flash=True, flash_ms=120)
        _, params = transport.byte_calls[0]
        assert params == {"size": "xga", "warmup": "2", "flash": "1",
                          "flash_ms": "120", "token": "tok"}

    async def test_a_bad_option_never_reaches_the_network(self):
        transport = FakeTransport()
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json)
        with pytest.raises(ValueError):
            await eye.capture(size="nope")
        assert transport.byte_calls == []

    async def test_a_bad_frame_is_rejected_at_the_boundary(self):
        transport = FakeTransport(frame=b"<html>oops</html>")
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json)
        with pytest.raises(NoFrame):
            await eye.capture()

    async def test_presence_reads_the_motion_endpoint(self):
        transport = FakeTransport(json_reply={"enabled": True, "moved": True,
                                              "look": {"x": 40, "y": -10}})
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json)
        presence = await eye.presence()
        assert presence.somebody_there
        assert presence.look == (40, -10)
        assert transport.json_calls[0][0] == "/motion"

    async def test_presence_can_ask_for_a_re_baseline(self):
        transport = FakeTransport()
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json, token="tok")
        await eye.presence(reset=True)
        _, params = transport.json_calls[0]
        assert params == {"reset": "1", "token": "tok"}

    async def test_status_returns_a_plain_dict(self):
        transport = FakeTransport(json_reply={"camera": "front-eye", "resolution": "svga"})
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json)
        status = await eye.status()
        assert status["camera"] == "front-eye"

    async def test_a_non_dict_status_does_not_crash_the_caller(self):
        transport = FakeTransport(json_reply=["unexpected"])
        eye = CameraEye(transport.fetch_bytes, transport.fetch_json)
        assert await eye.status() == {}


class TestPresenceDefaults:
    def test_a_default_presence_reports_nobody(self):
        assert not Presence().somebody_there
