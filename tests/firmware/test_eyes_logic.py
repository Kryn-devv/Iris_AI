"""Exhaustive verification of the OLED eye engine's geometry and timing.

The firmware is C++ on an ESP32-S3, so it cannot run in CI. What can be
verified is the part where every real bug in an animation lives: the geometry
(does anything ever try to draw off the 128x64 panel, or hand Adafruit_GFX a
negative-height rectangle?) and the timing (does a blink always finish, does
the talking animation always expire, do the deadlines survive the millis()
wrap after 49.7 days?).

This module is a faithful transliteration of
``firmware/esp32-s3-iris-sensors/eyes.h`` and ``face.h``, including C integer
truncation, plus a check that the firmware's emotion table and IRIS's Python
copy of it have not drifted apart.

If either header is edited, update the transliteration in lockstep.
"""

from __future__ import annotations

import itertools
import math
import pathlib
import re
from dataclasses import dataclass, replace

import pytest

FIRMWARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "esp32-s3-iris-sensors"
EYES_H = (FIRMWARE_DIR / "eyes.h").read_text(encoding="utf-8")
FACE_H = (FIRMWARE_DIR / "face.h").read_text(encoding="utf-8")

EYE_W, EYE_H = 128, 64
EYE_CX, EYE_CY = EYE_W // 2, EYE_H // 2

STYLE_RECT, STYLE_ARC, STYLE_HEART, STYLE_CLOSED, STYLE_CROSS = range(5)

EMOTIONS = (
    "neutral", "happy", "excited", "love", "sad", "angry",
    "surprised", "sleepy", "thinking", "confused", "listening",
    "wink", "suspicious", "dizzy",
)

BLINK_MS = 110
SPEAK_MAX_MS = 30_000
EASE_NUM, EASE_DEN = 3, 16
UINT32 = 1 << 32


def c_div(numerator: int, denominator: int) -> int:
    """C integer division: truncates toward zero (Python's // floors)."""
    q = abs(numerator) // abs(denominator)
    return -q if (numerator < 0) != (denominator < 0) else q


# ---------------------------------------------------------------------------
# Transliteration of eyes.h
# ---------------------------------------------------------------------------


@dataclass
class EyePose:
    w: int = 88
    h: int = 56
    r: int = 20
    dx: int = 0
    dy: int = 0
    lid_top: int = 0
    lid_bot: int = 0
    brow_in: int = 0
    brow_out: int = 0
    arc_t: int = 14
    style: int = STYLE_RECT
    glint: int = 1


def pose_for(emotion: str, is_left: bool) -> EyePose:
    """firmware: poseFor()"""
    p = EyePose()
    if emotion == "happy":
        p.w, p.h, p.arc_t, p.dy = 96, 52, 16, 3
        p.style, p.glint = STYLE_ARC, 0
    elif emotion == "excited":
        p.w, p.h, p.r, p.dy, p.glint = 98, 60, 24, -2, 2
    elif emotion == "love":
        p.w, p.h, p.style, p.glint = 96, 58, STYLE_HEART, 0
    elif emotion == "sad":
        p.w, p.h, p.r, p.dy = 82, 42, 16, 9
        p.lid_top, p.brow_out = 8, 20
    elif emotion == "angry":
        p.w, p.h, p.r = 92, 40, 12
        p.lid_top, p.brow_in = 3, 26
    elif emotion == "surprised":
        p.w, p.h, p.r, p.glint = 74, 64, 32, 2
    elif emotion == "sleepy":
        p.w, p.h, p.r, p.dy = 86, 30, 13, 11
        p.lid_top, p.glint = 16, 0
    elif emotion == "thinking":
        p.w, p.h, p.r = 82, 48, 18
        p.dx, p.dy = -12, -7
        p.lid_top, p.brow_out = 7, 7
        if not is_left:
            p.h -= 8
            p.lid_top += 4
    elif emotion == "confused":
        if is_left:
            p.w, p.h, p.r, p.dy = 92, 58, 22, -3
        else:
            p.w, p.h, p.r, p.dy, p.brow_out = 74, 40, 16, 5, 12
    elif emotion == "listening":
        p.w, p.h, p.r, p.glint = 94, 60, 22, 1
    elif emotion == "wink":
        if is_left:
            p.style, p.h, p.glint = STYLE_CLOSED, 10, 0
        else:
            p.w, p.h, p.arc_t = 96, 52, 16
            p.style, p.glint = STYLE_ARC, 0
    elif emotion == "suspicious":
        p.w, p.h, p.r = 90, 32, 12
        p.lid_top, p.brow_in, p.glint = 20, 10, 0
    elif emotion == "dizzy":
        p.w, p.h, p.style, p.glint = 62, 62, STYLE_CROSS, 0
    return p


def clamp_pose(p: EyePose) -> EyePose:
    """firmware: clampPose()"""
    p = replace(p)
    p.w = max(8, min(EYE_W, p.w))
    p.h = max(2, min(EYE_H, p.h))

    max_r = min(p.w, p.h) // 2
    p.r = max(0, min(max_r, p.r))

    p.lid_top = max(0, p.lid_top)
    p.lid_bot = max(0, p.lid_bot)
    p.lid_top = min(p.lid_top, p.h)
    p.lid_bot = min(p.lid_bot, p.h - p.lid_top)
    p.brow_in = max(0, min(p.h, p.brow_in))
    p.brow_out = max(0, min(p.h, p.brow_out))
    p.arc_t = max(2, min(p.h, p.arc_t))

    slack_x = (EYE_W - p.w) // 2
    slack_y = (EYE_H - p.h) // 2
    p.dx = max(-slack_x, min(slack_x, p.dx))
    p.dy = max(-slack_y, min(slack_y, p.dy))
    return p


def eye_box(p: EyePose) -> tuple[int, int, int, int]:
    """The solid eye body's rectangle, as drawEye computes it."""
    cx, cy = EYE_CX + p.dx, EYE_CY + p.dy
    x0, y0 = cx - p.w // 2, cy - p.h // 2
    return (x0, y0, x0 + p.w, y0 + p.h)


def arc_geometry(p: EyePose) -> tuple[int, int, int]:
    """firmware: the STYLE_ARC branch -> (arc centre y, radius, trim height)."""
    cy = EYE_CY + p.dy
    rad = p.w // 2
    arc_cy = cy + rad // 2
    if arc_cy > EYE_H - 2:          # the clamp that stops a negative trim
        arc_cy = EYE_H - 2
    trim_y = arc_cy + 1
    trim_h = (EYE_H - trim_y) if trim_y < EYE_H else 0
    return (arc_cy, rad, trim_h)


def ease(current: int, target: int, num: int = EASE_NUM, den: int = EASE_DEN) -> int:
    """firmware: ease()"""
    delta = target - current
    if delta == 0:
        return current
    step = c_div(delta * num, den)
    if step == 0:
        step = 1 if delta > 0 else -1
    return current + step


# ---------------------------------------------------------------------------
# Transliteration of face.h timing
# ---------------------------------------------------------------------------


def face_deadline(now: int, ms: int) -> int:
    """firmware: faceDeadline(). 0 is the 'nothing pending' sentinel."""
    t = (now + ms) % UINT32
    return t if t else 1


def face_due(now: int, deadline: int) -> bool:
    """firmware: faceDue(). Signed 32-bit difference, so it survives the wrap."""
    if deadline == 0:
        return False
    diff = (now - deadline) % UINT32
    if diff >= 1 << 31:
        diff -= UINT32
    return diff >= 0


def blink_scale(now: int, blink_start: int) -> float:
    """firmware: blinkScale()"""
    if blink_start == 0:
        return 1.0
    elapsed = (now - blink_start) % UINT32
    if elapsed >= BLINK_MS:
        return 1.0
    half = BLINK_MS * 0.5
    k = (elapsed / half) if elapsed < half else ((BLINK_MS - elapsed) / half)
    return 1.0 - 0.94 * k


@dataclass
class BlinkMachine:
    """firmware: the blinkStartMs / nextBlinkAt / blinksQueued state machine."""

    blink_start: int = 0
    next_blink_at: int = 0
    queued: int = 0
    pair_second: bool = False
    completed: int = 0

    def request(self, now: int, count: int = 1) -> None:
        """firmware: blinkNow()"""
        if self.blink_start == 0:
            self.blink_start = now or 1
        self.queued = count - 1 if count > 1 else 0
        self.pair_second = False

    def advance(self, now: int, double: bool = False) -> None:
        """firmware: advanceBlink(). `double` stands in for the random roll."""
        if self.blink_start != 0:
            if (now - self.blink_start) % UINT32 >= BLINK_MS:
                self.blink_start = 0
                self.completed += 1
                if self.queued > 0:
                    self.queued -= 1
                    self.pair_second = True
                    self.next_blink_at = face_deadline(now, 70)
                else:
                    self.next_blink_at = face_deadline(now, 3000)
            return
        if face_due(now, self.next_blink_at):
            self.blink_start = now or 1
            self.next_blink_at = 0
            if self.pair_second:
                self.pair_second = False
            elif self.queued == 0 and double:
                self.queued = 1


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


class TestEmotionTable:
    def test_every_emotion_has_a_pose_for_both_eyes(self):
        for emotion in EMOTIONS:
            for is_left in (True, False):
                p = pose_for(emotion, is_left)
                assert p.w > 0 and p.h > 0
                assert p.style in range(5)

    def test_the_firmware_lists_exactly_these_emotions(self):
        """The header's name table is the contract the dashboard and IRIS use."""
        block = re.search(
            r"EMOTION_NAMES\[EMO_COUNT\]\s*=\s*\{(.*?)\};", EYES_H, re.S
        )
        assert block, "EMOTION_NAMES table not found in eyes.h"
        names = tuple(re.findall(r'"([a-z]+)"', block.group(1)))
        assert names == EMOTIONS

    def test_the_enum_and_the_name_table_are_the_same_length(self):
        """A short name table would read past the end of the array."""
        enum_block = re.search(r"enum Emotion\s*:\s*uint8_t\s*\{(.*?)\};", EYES_H, re.S)
        assert enum_block
        members = re.findall(r"\bEMO_([A-Z]+)\b", enum_block.group(1))
        members = [m for m in members if m != "COUNT"]
        assert len(members) == len(EMOTIONS)

    def test_asymmetric_emotions_really_differ_between_the_eyes(self):
        """A wink with matched eyes is not a wink."""
        for emotion in ("wink", "confused", "thinking"):
            left = pose_for(emotion, True)
            right = pose_for(emotion, False)
            assert left != right, f"{emotion} renders identically on both eyes"

    def test_symmetric_emotions_match_between_the_eyes(self):
        for emotion in ("neutral", "happy", "sad", "angry", "excited",
                        "love", "surprised", "sleepy", "listening",
                        "suspicious", "dizzy"):
            assert pose_for(emotion, True) == pose_for(emotion, False)

    def test_only_the_wink_closes_one_eye(self):
        for emotion in EMOTIONS:
            for is_left in (True, False):
                if pose_for(emotion, is_left).style == STYLE_CLOSED:
                    assert emotion == "wink" and is_left


class TestPoseClamping:
    """Nothing may ever be asked to draw off the panel."""

    def test_the_eye_body_stays_on_screen_for_every_emotion_and_gaze(self):
        for emotion in EMOTIONS:
            for is_left in (True, False):
                base = pose_for(emotion, is_left)
                for gx, gy in itertools.product(range(-40, 41, 4), repeat=2):
                    p = clamp_pose(replace(base, dx=base.dx + gx, dy=base.dy + gy))
                    x0, y0, x1, y1 = eye_box(p)
                    assert 0 <= x0 and x1 <= EYE_W, f"{emotion} x {x0}..{x1}"
                    assert 0 <= y0 and y1 <= EYE_H, f"{emotion} y {y0}..{y1}"

    def test_the_eye_body_stays_on_screen_for_absurd_input(self):
        for w, h in itertools.product((-50, 0, 1, 8, 87, 128, 400), repeat=2):
            for dx, dy in itertools.product((-999, -30, 0, 30, 999), repeat=2):
                p = clamp_pose(EyePose(w=w, h=h, dx=dx, dy=dy))
                x0, y0, x1, y1 = eye_box(p)
                assert 0 <= x0 and x1 <= EYE_W
                assert 0 <= y0 and y1 <= EYE_H

    def test_corner_radius_never_exceeds_half_the_shorter_side(self):
        """Adafruit_GFX corrupts its own arcs when it does."""
        for w, h, r in itertools.product((8, 20, 74, 128), (2, 10, 40, 64), (0, 5, 40, 999)):
            p = clamp_pose(EyePose(w=w, h=h, r=r))
            assert 0 <= p.r <= min(p.w, p.h) // 2

    def test_lids_may_meet_but_never_cross(self):
        for h in range(2, 65, 3):
            for lt, lb in itertools.product(range(0, 70, 7), repeat=2):
                p = clamp_pose(EyePose(h=h, lid_top=lt, lid_bot=lb))
                assert p.lid_top >= 0 and p.lid_bot >= 0
                assert p.lid_top + p.lid_bot <= p.h

    def test_clamping_is_idempotent(self):
        for emotion in EMOTIONS:
            once = clamp_pose(replace(pose_for(emotion, True), dx=99, dy=-99, r=999))
            assert clamp_pose(once) == once

    def test_every_emotion_declares_a_pose_it_can_actually_render(self):
        """A pose whose position fields the clamp has to correct is a table bug:
        it declares a lift or a droop the panel has no room for, so the eye
        never renders where the table says. (Fields a style does not read — a
        closed lid ignores the corner radius — may be tidied freely.)"""
        for emotion in EMOTIONS:
            for is_left in (True, False):
                p = pose_for(emotion, is_left)
                c = clamp_pose(p)
                for field in ("w", "h", "dx", "dy", "lid_top", "lid_bot",
                              "brow_in", "brow_out", "style", "glint"):
                    assert getattr(c, field) == getattr(p, field), (
                        f"{emotion} ({'left' if is_left else 'right'}): "
                        f"{field} {getattr(p, field)} -> {getattr(c, field)}"
                    )

    def test_the_clamp_only_ever_reduces_the_unused_shape_fields(self):
        for emotion in EMOTIONS:
            for is_left in (True, False):
                p = pose_for(emotion, is_left)
                c = clamp_pose(p)
                assert c.r <= p.r
                assert c.arc_t <= p.arc_t


class TestArcGeometry:
    """The happy squint hangs below its own eye box, so it needs its own check.

    A negative-height rectangle is drawn UPWARD by Adafruit_GFX, so the trim
    below the crescent would erase the crescent itself — a happy face looking
    down flickered as it blinked.
    """

    def test_trim_height_is_never_negative(self):
        for emotion in ("happy", "wink"):
            for is_left in (True, False):
                base = pose_for(emotion, is_left)
                if base.style != STYLE_ARC:
                    continue
                for blink in (1.0, 0.5, 0.2, 0.06):
                    h = max(2, int(base.h * blink))
                    for gy in range(-40, 41, 2):
                        p = clamp_pose(replace(base, h=h, dy=base.dy + gy))
                        _, _, trim_h = arc_geometry(p)
                        assert trim_h >= 0, f"{emotion} blink={blink} gy={gy}"

    def test_the_reported_case_happy_looking_down_mid_blink(self):
        base = pose_for("happy", True)
        p = clamp_pose(replace(base, h=26, dy=base.dy + 14 + 3))
        arc_cy, _, trim_h = arc_geometry(p)
        assert trim_h >= 0
        assert arc_cy <= EYE_H - 2

    def test_the_crescent_centre_stays_on_the_panel(self):
        for gy in range(-40, 41, 3):
            for h in (3, 26, 52, 62):
                p = clamp_pose(replace(pose_for("happy", True), h=h, dy=gy))
                arc_cy, _, _ = arc_geometry(p)
                assert 0 <= arc_cy <= EYE_H - 2

    def test_the_arc_clamp_is_present_in_the_firmware(self):
        assert "if (arcCy > EYE_H - 2) arcCy = EYE_H - 2;" in EYES_H
        assert "if (trimY < EYE_H)" in EYES_H


# ---------------------------------------------------------------------------
# Easing
# ---------------------------------------------------------------------------


class TestEasing:
    """A pose change must always finish. Integer easing alone stalls one pixel
    short forever, which shows as an eye that never quite closes."""

    def test_always_converges_and_never_stalls(self):
        for start, target in itertools.product(range(-40, 130, 11), repeat=2):
            value, steps = start, 0
            while value != target:
                nxt = ease(value, target)
                assert nxt != value, f"stalled at {value} heading for {target}"
                value = nxt
                steps += 1
                assert steps < 500
            assert value == target

    def test_never_overshoots(self):
        for target in (0, 1, 55, 128):
            value = 0 if target > 0 else 128
            while value != target:
                nxt = ease(value, target)
                if target > value:
                    assert nxt <= target
                else:
                    assert nxt >= target
                value = nxt

    def test_a_one_step_gap_closes_immediately(self):
        assert ease(10, 11) == 11
        assert ease(11, 10) == 10

    def test_a_big_change_settles_in_roughly_a_third_of_a_second(self):
        """At 40 fps. Slower reads as sluggish, faster reads as a jump cut."""
        value, frames = 0, 0
        while value < 88 * 9 // 10:
            value = ease(value, 88)
            frames += 1
        assert 5 <= frames <= 20, f"{frames} frames to 90%"

    def test_style_switches_only_once_the_size_has_caught_up(self):
        """Blending a heart into a rectangle produced a visible glitch frame."""
        assert 'if (abs((int)cur.h - (int)want.h) < 8 && abs((int)cur.w - (int)want.w) < 8)' in EYES_H


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


class TestBlinking:
    def test_blink_scale_stays_within_range(self):
        for elapsed in range(0, BLINK_MS + 40):
            scale = blink_scale(1000 + elapsed, 1000)
            assert 0.0 < scale <= 1.0

    def test_blink_closes_then_opens(self):
        start = 1000
        closed = blink_scale(start + BLINK_MS // 2, start)
        assert closed < 0.2, "the eye should be nearly shut halfway through"
        assert blink_scale(start, start) == pytest.approx(1.0)
        assert blink_scale(start + BLINK_MS, start) == pytest.approx(1.0)

    def test_blink_is_symmetric(self):
        start = 1000
        for offset in range(0, BLINK_MS // 2):
            assert blink_scale(start + offset, start) == pytest.approx(
                blink_scale(start + BLINK_MS - offset, start), abs=0.02
            )

    def test_eyes_never_fully_vanish(self):
        """A height of exactly 0 draws nothing at all, which reads as a fault."""
        for elapsed in range(0, BLINK_MS):
            assert blink_scale(1000 + elapsed, 1000) > 0.0

    def test_a_blink_always_completes(self):
        m = BlinkMachine()
        m.request(now=1000)
        now = 1000
        for _ in range(200):
            now += 25
            m.advance(now)
            if m.completed:
                break
        assert m.completed == 1
        assert m.blink_start == 0

    def test_a_queued_double_blink_actually_fires(self):
        """The bug this pins: a queued blink was scheduled by pre-setting the
        START time into the future. blinkScale measures now-start as unsigned,
        so a future start read as an enormous elapsed time and the second blink
        was over before it began — the pair silently became a single blink."""
        m = BlinkMachine()
        m.request(now=1000, count=2)
        now = 1000
        for _ in range(400):
            now += 10
            m.advance(now)
            if m.completed >= 2:
                break
        assert m.completed == 2, "the second half of the double blink never ran"

    def test_a_pair_is_exactly_two_blinks_never_a_chain(self):
        """With the random roll rigged to always say "pair", blinks must still
        arrive in groups of two. A queued blink that re-queues another would
        make the eyes flutter continuously."""
        m = BlinkMachine()
        now, times = 1000, []
        m.next_blink_at = face_deadline(now, 500)   # face.h: begin() schedules one
        for _ in range(2000):
            now += 5
            before = m.completed
            m.advance(now, double=True)
            if m.completed > before:
                times.append(now)
        assert len(times) >= 4, "not enough blinks observed to judge grouping"

        # Split into runs of blinks closer together than any idle gap.
        runs, run = [], [times[0]]
        for previous, current in zip(times, times[1:]):
            if current - previous <= 400:
                run.append(current)
            else:
                runs.append(run)
                run = [current]
        runs.append(run)
        assert max(len(r) for r in runs) == 2, f"blink runs: {[len(r) for r in runs]}"

    def test_the_firmware_distinguishes_first_from_second_of_a_pair(self):
        """Testing the counter alone could not: by the time the second blink
        starts the counter is back at 0, so it rolled again and pairs chained."""
        assert "bool     pairSecond" in FACE_H
        assert "if (pairSecond) {" in FACE_H

    def test_queued_count_never_exceeds_one(self):
        m = BlinkMachine()
        now = 1000
        m.next_blink_at = face_deadline(now, 500)
        for _ in range(1000):
            now += 5
            m.advance(now, double=True)
            assert m.queued <= 1

    def test_blink_survives_the_millis_wrap(self):
        start = UINT32 - 50
        now = (start + BLINK_MS // 2) % UINT32
        assert blink_scale(now, start) < 0.2
        assert blink_scale((start + BLINK_MS) % UINT32, start) == pytest.approx(1.0)


class TestRolloverSafeDeadlines:
    """millis() wraps every ~49.7 days. A face frozen mid-blink after seven
    weeks of uptime is exactly the kind of bug nobody reproduces on a bench."""

    def naive(self, now: int, deadline: int) -> bool:
        return now >= deadline

    def test_agrees_with_the_naive_form_away_from_the_wrap(self):
        for now in range(1000, 100_000, 997):
            for offset in (-5000, -1, 0, 1, 5000):
                deadline = now + offset
                if 0 < deadline < UINT32:
                    assert face_due(now, deadline) == self.naive(now, deadline)

    def test_fires_across_the_wrap_where_the_naive_form_fails(self):
        deadline = UINT32 - 500
        now = 200
        assert face_due(now, deadline) is True
        assert self.naive(now, deadline) is False

    def test_not_yet_due_across_the_wrap(self):
        assert face_due(UINT32 - 500, 200) is False

    def test_exhaustive_around_the_wrap(self):
        for offset in range(-2000, 2001, 7):
            deadline = offset % UINT32
            if deadline == 0:
                continue
            for delta in range(-1200, 1201, 11):
                now = (deadline + delta) % UINT32
                assert face_due(now, deadline) == (delta >= 0)

    def test_zero_means_nothing_pending(self):
        for now in (0, 1, 5000, UINT32 - 1):
            assert face_due(now, 0) is False

    def test_a_deadline_never_lands_on_the_sentinel(self):
        for ms in (1, 2, 70, 3000, SPEAK_MAX_MS):
            for now in range(UINT32 - 4000, UINT32):
                assert face_deadline(now, ms) != 0


class TestSpeakingAnimation:
    """The talking bounce must stop on its own.

    IRIS sends the estimated duration up front precisely so a lost "finished
    speaking" packet cannot leave the eyes bouncing indefinitely — the same
    reasoning as the robot base auto-stopping rather than trusting a stop.
    """

    def test_speaking_expires_without_any_stop_message(self):
        now = 5000
        until = face_deadline(now, 2500)
        assert face_due(now + 2499, until) is False
        assert face_due(now + 2501, until) is True

    def test_the_firmware_caps_the_duration(self):
        assert "#define SPEAK_MAX_MS" in FACE_H
        assert "if (ms > SPEAK_MAX_MS) ms = SPEAK_MAX_MS;" in FACE_H

    def test_zero_stops_immediately(self):
        assert "if (ms == 0) { speakUntil = 0;" in FACE_H

    def test_speaking_expiry_survives_the_wrap(self):
        now = UINT32 - 1000
        until = face_deadline(now, 2500)
        assert face_due((now + 2400) % UINT32, until) is False
        assert face_due((now + 2600) % UINT32, until) is True


class TestIdleBehaviour:
    def test_any_command_wakes_the_face(self):
        """dozing must clear on contact, not only on an emotion change."""
        assert "dozing = false;         /* any contact at all wakes the face up */" in FACE_H

    def test_a_held_emotion_reverts_so_it_cannot_stick(self):
        assert "if (faceDue(now, holdUntil)) { emotion = revertTo; holdUntil = 0; }" in FACE_H

    def test_the_animation_keeps_running_while_wifi_is_joining(self):
        """A face frozen at boot looks broken, and boot is when it is watched."""
        ino = (FIRMWARE_DIR / "esp32-s3-iris-sensors.ino").read_text(encoding="utf-8")
        join_loop = ino.split("const unsigned long joinDeadline")[1].split("if (WiFi.status()")[0]
        assert "animateOnce()" in join_loop

        # ...and that helper really is what advances and draws the face, so the
        # call above is not just a name that happens to look right.
        helper = ino.split("static void animateOnce()")[1].split("\n}")[0]
        assert "face.tick(" in helper and "drawFace(" in helper

    def test_the_face_keeps_animating_while_the_board_is_talking(self):
        """Uploading a phrase and playing a reply both block for seconds. The
        eyes are pumped throughout, which is what makes the talking bounce
        visible at all rather than a frozen frame."""
        ino = (FIRMWARE_DIR / "esp32-s3-iris-sensors.ino").read_text(encoding="utf-8")
        assert "voice.onTick(animateOnce)" in ino
        voice_h = (FIRMWARE_DIR / "voice.h").read_text(encoding="utf-8")
        assert "if (tickCb_) tickCb_();" in voice_h

    def test_a_reply_drives_the_talking_bounce_for_its_own_length(self):
        """Bounded by the firmware, so a lost packet cannot leave it bouncing."""
        ino = (FIRMWARE_DIR / "esp32-s3-iris-sensors.ino").read_text(encoding="utf-8")
        assert "voice.onSpeaking(onReplyStarting)" in ino
        handler = ino.split("static void onReplyStarting(uint32_t ms)")[1].split("\n}")[0]
        assert "face.setSpeaking(ms" in handler


# ---------------------------------------------------------------------------
# Firmware / Python agreement
# ---------------------------------------------------------------------------


class TestFirmwareAndPythonAgree:
    """IRIS validates an emotion name before sending it, so its list and the
    firmware's must not drift. A mismatch shows up as a working command that
    the board answers with HTTP 400."""

    def test_emotion_lists_are_identical(self):
        from iris.app.tools.devices.face import EMOTIONS as PY_EMOTIONS
        assert tuple(PY_EMOTIONS) == EMOTIONS

    def test_iris_only_ever_sends_names_the_firmware_accepts(self):
        """IRIS resolves synonyms itself and sends the canonical name, so the
        contract is that every canonical name exists in the firmware's table —
        not that the two synonym lists match. The firmware's own synonyms are a
        convenience for URLs typed by hand."""
        from iris.app.tools.devices.face import EMOTIONS as PY, normalize_emotion, SYNONYMS
        for word in list(SYNONYMS) + list(PY):
            canonical = normalize_emotion(word)
            assert canonical is not None, f"'{word}' does not resolve"
            assert canonical in EMOTIONS, (
                f"IRIS would send '{canonical}' for '{word}', which the board rejects"
            )

    def test_the_firmware_synonyms_all_resolve_to_a_real_emotion(self):
        """A synonym line's return may sit on the following line, so scan
        forward from each match rather than assuming one line per rule."""
        seen = 0
        for match in re.finditer(r'wanted == "([a-z]+)"', EYES_H):
            target = re.search(r"return (EMO_[A-Z]+);", EYES_H[match.end():match.end() + 200])
            assert target, f"synonym '{match.group(1)}' has no return nearby"
            name = target.group(1).replace("EMO_", "").lower()
            assert name in EMOTIONS, f"firmware maps '{match.group(1)}' to unknown '{name}'"
            seen += 1
        assert seen >= 10, f"only found {seen} firmware synonyms — regex drifted?"

    def test_python_synonyms_all_resolve_to_a_real_emotion(self):
        from iris.app.tools.devices.face import SYNONYMS
        for word, target in SYNONYMS.items():
            assert target in EMOTIONS, f"'{word}' maps to unknown '{target}'"

    def test_the_speak_ceiling_matches_the_firmware(self):
        from iris.app.tools.devices.face import MAX_SPEAK_MS
        match = re.search(r"#define SPEAK_MAX_MS\s+(\d+)", FACE_H)
        assert match, "SPEAK_MAX_MS not found"
        assert MAX_SPEAK_MS == int(match.group(1))

    def test_the_panel_size_matches_the_firmware(self):
        assert f"#define EYE_W        {EYE_W}" in EYES_H
        assert f"#define EYE_H        {EYE_H}" in EYES_H


# ---------------------------------------------------------------------------
# The full per-frame pipeline: pose -> modulate -> clamp -> draw
# ---------------------------------------------------------------------------


def modulate(
    p: EyePose,
    emotion: str,
    now_ms: int,
    *,
    speaking: bool = False,
    dozing: bool = False,
    blink_start: int = 0,
) -> EyePose:
    """firmware: FaceAnimator::modulate().

    Breathing, the talking bounce, the per-emotion life and the blink are
    applied to a COPY of the eased pose — they are per-frame modulations, not
    targets, so easing them would smear them away. C float-to-int conversion
    truncates toward zero, which Python's int() also does.
    """
    p = replace(p)
    t = now_ms * 0.001
    h_mul = 1.0 + 0.035 * math.sin(t * 1.6)      # always breathing
    w_mul = 1.0
    dx_add = dy_add = 0

    if speaking:
        env = (0.55 * math.sin(t * 13.0)
               + 0.30 * math.sin(t * 21.7)
               + 0.15 * math.sin(t * 7.3))
        h_mul *= 1.0 + 0.13 * env
        dy_add += int(3.0 * env)

    if emotion == "listening":
        w_mul *= 1.0 + 0.030 * math.sin(t * 6.0)
    elif emotion == "excited":
        h_mul *= 1.0 + 0.045 * math.sin(t * 9.5)
    elif emotion == "love":
        beat = math.sin(t * 5.0)
        h_mul *= 1.0 + 0.07 * beat
        w_mul *= 1.0 + 0.07 * beat
    elif emotion == "dizzy":
        dx_add += int(6.0 * math.sin(t * 8.0))
        dy_add += int(4.0 * math.cos(t * 11.0))
    elif emotion == "sad":
        dy_add += int(1.5 + 1.5 * math.sin(t * 1.1))

    if dozing:
        h_mul *= 0.45
        dy_add += 8 + int(2.0 * math.sin(t * 0.9))

    h_mul *= blink_scale(now_ms, blink_start)

    p.h = int(p.h * h_mul)
    p.w = int(p.w * w_mul)
    p.dy += dy_add
    p.dx += dx_add
    return p


def render_pose(emotion: str, is_left: bool, now_ms: int, gaze=(0, 0), **kwargs) -> EyePose:
    """One frame, exactly as the sketch produces it before drawEye."""
    p = pose_for(emotion, is_left)
    p.dx += gaze[0]
    p.dy += gaze[1]
    p = modulate(p, emotion, now_ms, **kwargs)
    return clamp_pose(p)


class TestFullFramePipeline:
    """The end-to-end invariant: whatever the animation layers do, drawEye is
    handed a pose that fits the panel. The arc-trim bug lived exactly here —
    the eye box was in bounds but a shape hanging below it was not."""

    TIMES = (0, 37, 250, 611, 1000, 2500, 7777, 60_000, 1 << 20)

    def test_eye_body_stays_on_screen_across_the_whole_pipeline(self):
        for emotion in EMOTIONS:
            for is_left in (True, False):
                for now in self.TIMES:
                    for speaking, dozing in itertools.product((False, True), repeat=2):
                        for gx, gy in ((0, 0), (26, 14), (-26, -14), (26, -14)):
                            p = render_pose(emotion, is_left, now, (gx, gy),
                                            speaking=speaking, dozing=dozing)
                            x0, y0, x1, y1 = eye_box(p)
                            assert 0 <= x0 and x1 <= EYE_W, f"{emotion} x {x0}..{x1}"
                            assert 0 <= y0 and y1 <= EYE_H, f"{emotion} y {y0}..{y1}"

    def test_arc_trim_never_negative_across_the_whole_pipeline(self):
        for emotion in ("happy", "wink"):
            for is_left in (True, False):
                if pose_for(emotion, is_left).style != STYLE_ARC:
                    continue
                for now in self.TIMES:
                    for blink_offset in (0, 20, 55, 90, 109):
                        for gy in (-14, 0, 7, 14):
                            p = render_pose(emotion, is_left, now, (0, gy),
                                            speaking=True,
                                            blink_start=max(1, now - blink_offset))
                            _, _, trim_h = arc_geometry(p)
                            assert trim_h >= 0, f"{emotion} now={now} gy={gy}"

    def test_lids_never_sum_past_the_eye_height_across_the_pipeline(self):
        """Both lids are drawn as BLACK rects over the white body, so an
        overshoot erases the eye instead of narrowing it."""
        for emotion in EMOTIONS:
            for is_left in (True, False):
                for now in self.TIMES:
                    for blink_offset in (0, 30, 55, 80, 109):
                        p = render_pose(emotion, is_left, now,
                                        blink_start=max(1, now - blink_offset),
                                        dozing=True)
                        assert p.lid_top + p.lid_bot <= p.h
                        assert p.lid_top >= 0 and p.lid_bot >= 0

    def test_the_eye_is_never_reduced_to_nothing(self):
        for emotion in EMOTIONS:
            for is_left in (True, False):
                for now in self.TIMES:
                    for blink_offset in (0, 55, 109):
                        p = render_pose(emotion, is_left, now,
                                        blink_start=max(1, now - blink_offset),
                                        dozing=True, speaking=True)
                        assert p.w >= 8 and p.h >= 2

    def test_breathing_actually_moves_something(self):
        """A face that never changes between frames is a bitmap, not a face."""
        heights = {render_pose("neutral", True, t).h for t in range(0, 4000, 120)}
        assert len(heights) > 1, "the neutral eye never changes size"

    def test_speaking_moves_more_than_idle_breathing(self):
        idle = {render_pose("neutral", True, t).h for t in range(0, 3000, 60)}
        talking = {render_pose("neutral", True, t, speaking=True).h
                   for t in range(0, 3000, 60)}
        assert max(talking) - min(talking) > max(idle) - min(idle)
