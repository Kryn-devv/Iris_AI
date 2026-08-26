"""Exhaustive verification of the BTS7960 robot firmware's control logic.

The firmware itself is C++ on an ESP32, so it cannot run in CI. What CAN be
verified — and is where every real bug in motor code hides — is the pure
logic: the direction table, the calibration pipeline (swap -> invert -> trim
-> deadband), the PWM pin encoding, ramping and the failsafe timers.

This module is a faithful, line-by-line transliteration of those functions
from ``firmware/esp32-iris-node-bts7960/esp32-iris-node-bts7960.ino``,
including C integer-truncation semantics, plus a physical model of a
4-wheel skid-steer robot. Together they let us assert the property that
actually matters to a user staring at a robot that turns the wrong way:

    for EVERY possible way the two driver modules can be wired
    (either module on either side, either polarity), some calibration
    setting makes "forward" go forward and "left" turn left.

If the firmware logic is edited, update the transliteration in lockstep.
"""

from __future__ import annotations

import itertools
import pathlib
from dataclasses import dataclass, replace

import pytest

PWM_DUTY_MAX = 255


def c_div_trunc(numerator: int, denominator: int) -> int:
    """C/C++ integer division: truncates toward zero (Python's // floors)."""
    quotient = abs(numerator) // abs(denominator)
    negative = (numerator < 0) != (denominator < 0)
    return -quotient if negative else quotient


# ---------------------------------------------------------------------------
# Transliteration of the firmware
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """Mirror of the firmware's Config struct (calibration fields only)."""

    swap_sides: bool = False
    invert_a: bool = False
    invert_b: bool = False
    trim_a: int = 100
    trim_b: int = 100
    min_duty: int = 0
    ramp_ms: int = 180
    failsafe_ms: int = 10000
    default_speed: int = 200


def clamp_speed(value: int) -> int:
    """firmware: clampSpeed()"""
    return max(0, min(PWM_DUTY_MAX, value))


def direction_to_pair(direction: str, speed: int) -> tuple[int, int] | None:
    """firmware: directionToPair() -> logical (left, right), or None if unknown."""
    if direction == "forward":
        return (speed, speed)
    if direction == "backward":
        return (-speed, -speed)
    if direction == "left":
        return (-speed, speed)
    if direction == "right":
        return (speed, -speed)
    return None


def apply_logical(cfg: Config, left: int, right: int) -> tuple[int, int]:
    """firmware: applyLogical() -> physical (targetA, targetB)."""
    a = right if cfg.swap_sides else left
    b = left if cfg.swap_sides else right
    if cfg.invert_a:
        a = -a
    if cfg.invert_b:
        b = -b
    a = c_div_trunc(a * cfg.trim_a, 100)
    b = c_div_trunc(b * cfg.trim_b, 100)
    if cfg.min_duty:
        if a != 0 and abs(a) < cfg.min_duty:
            a = cfg.min_duty if a > 0 else -cfg.min_duty
        if b != 0 and abs(b) < cfg.min_duty:
            b = cfg.min_duty if b > 0 else -cfg.min_duty
    return (a, b)


def write_side(signed_duty: int, brake: bool = False) -> tuple[int, int]:
    """firmware: writeSide() -> the (RPWM duty, LPWM duty) actually written."""
    if brake:
        return (PWM_DUTY_MAX, PWM_DUTY_MAX)
    duty = max(-PWM_DUTY_MAX, min(PWM_DUTY_MAX, signed_duty))
    if duty >= 0:
        return (duty, 0)
    return (0, -duty)


def ramp_step(cfg: Config, dt_ms: int) -> int:
    """firmware: the per-tick step size inside rampTick()."""
    if cfg.ramp_ms <= 0:
        return PWM_DUTY_MAX
    return max(1, c_div_trunc(PWM_DUTY_MAX * dt_ms, cfg.ramp_ms))


def ramp_tick(live: int, target: int, step: int) -> int:
    """firmware: one axis of rampTick()."""
    if live == target:
        return live
    if abs(target - live) <= step:
        return target
    return live + (step if target > live else -step)


def arcade_mix(y: int, x: int) -> tuple[int, int]:
    """firmware: handleDrive() mixing."""
    left = max(-PWM_DUTY_MAX, min(PWM_DUTY_MAX, y + x))
    right = max(-PWM_DUTY_MAX, min(PWM_DUTY_MAX, y - x))
    return (left, right)


# ---------------------------------------------------------------------------
# Physical model of the robot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Wiring:
    """How the hardware actually got built (unknown to the firmware).

    ``module_a_is_left``  — module A's motors are on the robot's left side.
    ``a_rpwm_forward``    — energising module A's RPWM drives its wheels the
                            direction that pushes the robot forward.
    """

    module_a_is_left: bool
    a_rpwm_forward: bool
    b_rpwm_forward: bool


ALL_WIRINGS = [
    Wiring(*combo) for combo in itertools.product([True, False], repeat=3)
]

ALL_CALIBRATIONS = [
    Config(swap_sides=s, invert_a=ia, invert_b=ib)
    for s, ia, ib in itertools.product([True, False], repeat=3)
]


def robot_motion(cfg: Config, wiring: Wiring, direction: str, speed: int) -> tuple[int, int]:
    """Signed thrust of (left wheels, right wheels); positive pushes forward."""
    pair = direction_to_pair(direction, speed)
    assert pair is not None
    target_a, target_b = apply_logical(cfg, *pair)

    def side_thrust(signed: int, rpwm_forward: bool) -> int:
        rpwm, lpwm = write_side(signed)
        # RPWM spins one way, LPWM the other; polarity depends on the wiring.
        thrust = rpwm - lpwm
        return thrust if rpwm_forward else -thrust

    a_thrust = side_thrust(target_a, wiring.a_rpwm_forward)
    b_thrust = side_thrust(target_b, wiring.b_rpwm_forward)
    if wiring.module_a_is_left:
        return (a_thrust, b_thrust)
    return (b_thrust, a_thrust)


EXPECTED = {
    #            (left thrust sign, right thrust sign)
    "forward":  (+1, +1),
    "backward": (-1, -1),
    "left":     (-1, +1),
    "right":    (+1, -1),
}


def motion_is_correct(cfg: Config, wiring: Wiring, speed: int = 200) -> bool:
    """True when all four commands move the robot the way their name says."""
    for direction, (want_left, want_right) in EXPECTED.items():
        left, right = robot_motion(cfg, wiring, direction, speed)
        if (left > 0) != (want_left > 0) or (right > 0) != (want_right > 0):
            return False
        if abs(left) != speed or abs(right) != speed:
            return False
    return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDirectionTable:
    def test_forward_and_backward_drive_both_sides_together(self):
        for speed in range(0, 256):
            assert direction_to_pair("forward", speed) == (speed, speed)
            assert direction_to_pair("backward", speed) == (-speed, -speed)

    def test_turns_are_exact_mirrors(self):
        for speed in range(0, 256):
            left = direction_to_pair("left", speed)
            right = direction_to_pair("right", speed)
            assert left == (-speed, speed)
            assert right == (speed, -speed)
            assert left == (-right[0], -right[1])

    @pytest.mark.parametrize("bogus", ["", "up", "fwd", "FORWARD", "stop", "brake", "spin"])
    def test_unknown_directions_are_rejected(self, bogus):
        assert direction_to_pair(bogus, 200) is None


class TestPwmEncoding:
    def test_positive_duty_uses_rpwm_only(self):
        for duty in range(1, 256):
            rpwm, lpwm = write_side(duty)
            assert (rpwm, lpwm) == (duty, 0)

    def test_negative_duty_uses_lpwm_only(self):
        for duty in range(1, 256):
            rpwm, lpwm = write_side(-duty)
            assert (rpwm, lpwm) == (0, duty)

    def test_both_pwm_never_high_together_when_driving(self):
        """Both high on a BTS7960 is a brake, never a drive state."""
        for duty in range(-255, 256):
            rpwm, lpwm = write_side(duty)
            assert rpwm == 0 or lpwm == 0

    def test_zero_is_coast(self):
        assert write_side(0) == (0, 0)

    def test_brake_drives_both_high(self):
        for duty in (-255, -1, 0, 1, 255):
            assert write_side(duty, brake=True) == (255, 255)

    def test_out_of_range_duty_is_clamped(self):
        assert write_side(9999) == (255, 0)
        assert write_side(-9999) == (0, 255)


class TestCalibrationPipeline:
    def test_identity_config_passes_through(self):
        cfg = Config()
        for left, right in itertools.product(range(-255, 256, 5), repeat=2):
            assert apply_logical(cfg, left, right) == (left, right)

    def test_swap_exchanges_sides(self):
        cfg = Config(swap_sides=True)
        for left, right in itertools.product(range(-255, 256, 17), repeat=2):
            assert apply_logical(cfg, left, right) == (right, left)

    def test_invert_negates_only_its_own_side(self):
        for left, right in itertools.product(range(-255, 256, 31), repeat=2):
            assert apply_logical(Config(invert_a=True), left, right) == (-left, right)
            assert apply_logical(Config(invert_b=True), left, right) == (left, -right)
            assert apply_logical(Config(invert_a=True, invert_b=True), left, right) == (-left, -right)

    def test_swap_happens_before_invert(self):
        """Invert must follow the physical module, not the logical side."""
        cfg = Config(swap_sides=True, invert_a=True)
        # A receives 'right', then is inverted; B receives 'left' untouched.
        assert apply_logical(cfg, 100, 40) == (-40, 100)

    def test_trim_scales_and_truncates_toward_zero(self):
        assert apply_logical(Config(trim_a=50), 200, 200) == (100, 200)
        assert apply_logical(Config(trim_b=50), 200, 200) == (200, 100)
        assert apply_logical(Config(trim_a=90), 201, 0) == (180, 0)      # 180.9 -> 180
        assert apply_logical(Config(trim_a=90), -201, 0) == (-180, 0)    # toward zero
        assert apply_logical(Config(trim_a=0), 255, 255) == (0, 255)

    def test_trim_never_flips_a_sign(self):
        for trim in range(0, 101):
            for value in range(-255, 256, 13):
                a, _ = apply_logical(Config(trim_a=trim), value, 0)
                assert a == 0 or (a > 0) == (value > 0)

    def test_min_duty_lifts_small_values_but_never_wakes_a_stopped_side(self):
        cfg = Config(min_duty=60)
        assert apply_logical(cfg, 10, -10) == (60, -60)
        assert apply_logical(cfg, 0, 0) == (0, 0)          # stop stays stopped
        assert apply_logical(cfg, 200, -200) == (200, -200)

    def test_min_duty_preserves_direction(self):
        for min_duty in (0, 30, 60, 120):
            for value in range(-255, 256, 7):
                a, _ = apply_logical(Config(min_duty=min_duty), value, 0)
                if value == 0:
                    assert a == 0
                else:
                    assert (a > 0) == (value > 0)


class TestCalibrationCanFixAnyWiring:
    """The property that matters when a robot turns the wrong way."""

    def test_every_wiring_has_at_least_one_working_calibration(self):
        for wiring in ALL_WIRINGS:
            working = [c for c in ALL_CALIBRATIONS if motion_is_correct(c, wiring)]
            assert working, f"no calibration fixes {wiring}"

    def test_the_working_calibration_is_unique(self):
        """Exactly one setting per wiring, so the web toggles cannot be ambiguous."""
        for wiring in ALL_WIRINGS:
            working = [c for c in ALL_CALIBRATIONS if motion_is_correct(c, wiring)]
            assert len(working) == 1, f"{wiring} has {len(working)} valid calibrations"

    def test_default_calibration_matches_the_documented_wiring(self):
        """Wire it as the docs say and it works with zero calibration."""
        documented = Wiring(module_a_is_left=True, a_rpwm_forward=True, b_rpwm_forward=True)
        assert motion_is_correct(Config(), documented)

    def test_reported_symptom_is_a_wiring_case_the_calibration_covers(self):
        """A swapped pair of modules reads exactly as 'forward turns right'."""
        swapped = Wiring(module_a_is_left=False, a_rpwm_forward=True, b_rpwm_forward=True)
        assert not motion_is_correct(Config(), swapped)          # the bug as reported
        assert motion_is_correct(Config(swap_sides=True), swapped)  # one toggle fixes it

    def test_all_wirings_and_speeds(self):
        for wiring in ALL_WIRINGS:
            cfg = next(c for c in ALL_CALIBRATIONS if motion_is_correct(c, wiring))
            for speed in range(1, 256, 3):
                assert motion_is_correct(cfg, wiring, speed=speed)


class TestRamping:
    def test_converges_to_target_without_overshoot(self):
        cfg = Config(ramp_ms=180)
        step = ramp_step(cfg, 10)
        for target in range(-255, 256, 5):
            live = 0
            for _ in range(500):
                previous = live
                live = ramp_tick(live, target, step)
                assert abs(live) <= 255
                if target >= 0:
                    assert live <= target
                else:
                    assert live >= target
                if live == target:
                    break
                assert live != previous, "ramp stalled"
            assert live == target

    def test_monotonic_toward_target(self):
        step = ramp_step(Config(ramp_ms=200), 10)
        live, target = -255, 255
        history = [live]
        while live != target:
            live = ramp_tick(live, target, step)
            history.append(live)
        assert history == sorted(history)

    def test_zero_ramp_is_instant(self):
        step = ramp_step(Config(ramp_ms=0), 10)
        for target in (-255, -1, 0, 1, 255):
            assert ramp_tick(0, target, step) == target

    def test_step_is_never_zero(self):
        """A zero step would freeze the ramp forever."""
        for ramp_ms in range(1, 3001, 7):
            for dt in range(1, 60):
                assert ramp_step(Config(ramp_ms=ramp_ms), dt) >= 1

    def test_full_scale_ramp_takes_about_the_configured_time(self):
        cfg = Config(ramp_ms=200)
        dt = 10
        step = ramp_step(cfg, dt)
        live, ticks = 0, 0
        while live != 255:
            live = ramp_tick(live, 255, step)
            ticks += 1
        elapsed = ticks * dt
        assert 0.6 * cfg.ramp_ms <= elapsed <= 2.2 * cfg.ramp_ms


class TestArcadeMixing:
    def test_pure_forward_and_pure_turn(self):
        assert arcade_mix(200, 0) == (200, 200)
        assert arcade_mix(0, 200) == (200, -200)
        assert arcade_mix(0, -200) == (-200, 200)
        assert arcade_mix(0, 0) == (0, 0)

    def test_always_within_range(self):
        for y, x in itertools.product(range(-255, 256, 5), repeat=2):
            left, right = arcade_mix(y, x)
            assert -255 <= left <= 255
            assert -255 <= right <= 255

    def test_symmetry(self):
        for y, x in itertools.product(range(-255, 256, 11), repeat=2):
            left, right = arcade_mix(y, x)
            mirror_left, mirror_right = arcade_mix(y, -x)
            assert (left, right) == (mirror_right, mirror_left)


class TestSpeedClamping:
    def test_clamps_into_range(self):
        assert clamp_speed(-100) == 0
        assert clamp_speed(0) == 0
        assert clamp_speed(255) == 255
        assert clamp_speed(9999) == 255

    def test_end_to_end_never_exceeds_pwm_range(self):
        """No config combination may ever emit a duty outside 0..255."""
        configs = [
            Config(swap_sides=s, invert_a=ia, invert_b=ib, trim_a=ta, trim_b=tb, min_duty=md)
            for s, ia, ib in itertools.product([True, False], repeat=3)
            for ta in (0, 55, 100)
            for tb in (0, 55, 100)
            for md in (0, 80)
        ]
        for cfg in configs:
            for direction in EXPECTED:
                for speed in (0, 1, 128, 255):
                    pair = direction_to_pair(direction, clamp_speed(speed))
                    for target in apply_logical(cfg, *pair):
                        rpwm, lpwm = write_side(target)
                        assert 0 <= rpwm <= 255
                        assert 0 <= lpwm <= 255
                        assert rpwm == 0 or lpwm == 0


class TestFailsafeTiming:
    def _tripped(self, cfg: Config, moving: bool, since_command_ms: int, selftest: bool = False) -> bool:
        """firmware: the failsafe condition in loop()."""
        return bool(moving and cfg.failsafe_ms and not selftest
                    and since_command_ms > cfg.failsafe_ms)

    def test_stops_only_after_the_window(self):
        cfg = Config(failsafe_ms=10000)
        assert not self._tripped(cfg, moving=True, since_command_ms=9999)
        assert self._tripped(cfg, moving=True, since_command_ms=10001)

    def test_idle_robot_is_never_tripped(self):
        cfg = Config(failsafe_ms=10000)
        for elapsed in (0, 10001, 10**6):
            assert not self._tripped(cfg, moving=False, since_command_ms=elapsed)

    def test_zero_disables(self):
        cfg = Config(failsafe_ms=0)
        assert not self._tripped(cfg, moving=True, since_command_ms=10**6)

    def test_selftest_is_exempt(self):
        cfg = Config(failsafe_ms=1000)
        assert not self._tripped(cfg, moving=True, since_command_ms=5000, selftest=True)


class TestRolloverSafeDeadlines:
    """millis() wraps every ~49.7 days; deadline maths must survive it.

    The firmware compares `(long)(millis() - deadline) >= 0` rather than
    `millis() >= deadline`. These tests pin the difference, because the naive
    form silently stops firing across the wrap — a pending timed stop would
    never arrive and a moving robot would keep moving.
    """

    UINT32 = 1 << 32
    INT32_MIN = -(1 << 31)

    def naive_elapsed(self, now: int, deadline: int) -> bool:
        return now >= deadline

    def safe_elapsed(self, now: int, deadline: int) -> bool:
        """Mirror of the firmware: signed 32-bit difference >= 0."""
        diff = (now - deadline) % self.UINT32
        if diff >= 1 << 31:
            diff += self.INT32_MIN * 2
        return diff >= 0

    def test_agrees_with_naive_away_from_the_wrap(self):
        for now in range(0, 100_000, 997):
            for offset in (-5000, -1, 0, 1, 5000):
                deadline = now + offset
                if 0 <= deadline < self.UINT32:
                    assert self.safe_elapsed(now, deadline) == self.naive_elapsed(now, deadline)

    def test_survives_the_wrap_where_naive_fails(self):
        deadline = (self.UINT32 - 500) % self.UINT32   # deadline just before wrap
        now = 200                                       # clock has wrapped past it
        assert self.safe_elapsed(now, deadline) is True
        assert self.naive_elapsed(now, deadline) is False   # the bug being avoided

    def test_not_yet_due_across_the_wrap(self):
        deadline = 200                    # just after the wrap
        now = self.UINT32 - 500           # still before it
        assert self.safe_elapsed(now, deadline) is False

    def test_exhaustive_around_the_wrap_boundary(self):
        for deadline_offset in range(-2000, 2001, 7):
            deadline = deadline_offset % self.UINT32
            for delta in range(-1500, 1501, 11):
                now = (deadline + delta) % self.UINT32
                assert self.safe_elapsed(now, deadline) == (delta >= 0)


class TestBrakeLatchAndCoast:
    """The hardware must be re-written whenever the BRAKE state changes.

    rampTick() only touches the bridges when something changed. If that
    "changed" test looks at the ramp values alone, clearing a brake while the
    ramp is already at zero updates the flags and leaves the wheels physically
    locked — state says idle, robot says otherwise. The firmware therefore
    folds the braking flags into the dirty check, which is what these tests pin.
    """

    def is_dirty(self, live_a, target_a, live_b, target_b, braking_a, braking_b,
                 wrote_brake_a, wrote_brake_b) -> bool:
        """Mirror of the firmware's rampTick() dirty condition."""
        changed = (braking_a != wrote_brake_a) or (braking_b != wrote_brake_b)
        if live_a != target_a:
            changed = True
        if live_b != target_b:
            changed = True
        return changed

    def test_clearing_a_brake_at_zero_still_writes(self):
        """The reported latch: brake engaged, then a zero-valued command."""
        assert self.is_dirty(0, 0, 0, 0, False, False, True, True) is True

    def test_engaging_a_brake_at_zero_writes(self):
        assert self.is_dirty(0, 0, 0, 0, True, True, False, False) is True

    def test_single_side_brake_change_is_enough(self):
        assert self.is_dirty(0, 0, 0, 0, True, False, False, False) is True
        assert self.is_dirty(0, 0, 0, 0, False, True, False, False) is True

    def test_steady_state_does_not_write(self):
        """No spurious writes when nothing changed."""
        assert self.is_dirty(0, 0, 0, 0, False, False, False, False) is False
        assert self.is_dirty(200, 200, 200, 200, False, False, False, False) is False
        assert self.is_dirty(0, 0, 0, 0, True, True, True, True) is False

    def test_ramp_movement_still_writes(self):
        assert self.is_dirty(0, 200, 0, 0, False, False, False, False) is True
        assert self.is_dirty(0, 0, 0, -200, False, False, False, False) is True

    def test_exhaustive_dirty_logic(self):
        """Dirty exactly when the ramp moved or a brake flag changed."""
        for la, ta in itertools.product([0, 100], repeat=2):
            for lb, tb in itertools.product([0, 100], repeat=2):
                for ba, bb, wa, wb in itertools.product([True, False], repeat=4):
                    expected = (la != ta) or (lb != tb) or (ba != wa) or (bb != wb)
                    assert self.is_dirty(la, ta, lb, tb, ba, bb, wa, wb) is expected


class TestStopSemantics:
    """Coast and brake are physically different on a BTS7960.

    With EN high, both inputs low turns the LOW-side FETs on and shorts the
    motor — a brake, not a coast. Genuine free-wheeling needs the bridges
    disabled, so the enable pins are part of the stop state.
    """

    def stop_state(self, brake: bool) -> dict:
        """Mirror of doStop(): what the hardware ends up holding."""
        if brake:
            return {"enables": True, "rpwm": PWM_DUTY_MAX, "lpwm": PWM_DUTY_MAX}
        return {"enables": False, "rpwm": 0, "lpwm": 0}

    def test_brake_shorts_the_motor_with_bridges_enabled(self):
        state = self.stop_state(brake=True)
        assert state["enables"] is True
        assert state["rpwm"] == state["lpwm"] == PWM_DUTY_MAX

    def test_coast_disables_the_bridges(self):
        state = self.stop_state(brake=False)
        assert state["enables"] is False, "EN high + inputs low is a brake, not a coast"
        assert state["rpwm"] == state["lpwm"] == 0

    def test_the_two_stops_differ(self):
        assert self.stop_state(True) != self.stop_state(False)


class TestRawTestValidation:
    """/test bypasses calibration, so a bad argument must never guess."""

    def parse_dir(self, raw: str) -> int | None:
        """Mirror of handleTest() after the validation fix."""
        if raw not in ("forward", "backward"):
            return None
        return -1 if raw == "backward" else 1

    @pytest.mark.parametrize("bogus", ["", "stop", "fwd", "FORWARD", "left", "brake", "back"])
    def test_bad_direction_is_rejected_not_treated_as_forward(self, bogus):
        assert self.parse_dir(bogus) is None

    def test_valid_directions(self):
        assert self.parse_dir("forward") == 1
        assert self.parse_dir("backward") == -1


# ---------------------------------------------------------------------------
# Strict argument parsing
# ---------------------------------------------------------------------------

MS_MAX = 600000
MIN_DUTY_MAX = 120


def parse_long(raw: str) -> int | None:
    """firmware: parseLong(). None means "not a number", never 0.

    Arduino's String::toInt() answers 0 for "", "abc" and "twelve". On this
    board a silent 0 means GPIO 0 (a strapping pin) or "no timed stop", so
    every numeric argument goes through this instead.
    """
    if not raw or len(raw) > 11:
        return None
    index, negative = 0, False
    if raw[0] in "+-":
        negative = raw[0] == "-"
        index = 1
    if index >= len(raw):
        return None
    value = 0
    for char in raw[index:]:
        if not ("0" <= char <= "9"):
            return None
        value = value * 10 + (ord(char) - ord("0"))
        if value > 2000000:
            return None
    return -value if negative else value


def arg_clamp(args: dict, name: str, lo: int, hi: int, fallback: int):
    """firmware: argClamp() -> (ok, value). Out of range is clamped."""
    if name not in args:
        return (True, fallback)
    value = parse_long(args[name])
    if value is None:
        return (False, fallback)
    return (True, max(lo, min(hi, value)))


def arg_range(args: dict, name: str, lo: int, hi: int, fallback: int):
    """firmware: argRange() -> (ok, value). Out of range is refused."""
    if name not in args:
        return (True, fallback)
    value = parse_long(args[name])
    if value is None or value < lo or value > hi:
        return (False, fallback)
    return (True, value)


def arg_bool(args: dict, name: str, fallback: bool):
    """firmware: argBool() -> (ok, value)."""
    if name not in args:
        return (True, fallback)
    raw = args[name]
    if raw in ("true", "on", "yes"):
        return (True, True)
    if raw in ("false", "off", "no"):
        return (True, False)
    value = parse_long(raw)
    if value is None:
        return (False, fallback)
    return (True, value != 0)


class TestStrictArgumentParsing:
    """A typo must be an error, never a silent default.

    ``toInt()`` turning "twelve" into 0 is how a mistyped GPIO becomes GPIO 0
    and a mistyped duration becomes "run until the failsafe notices".
    """

    @pytest.mark.parametrize("raw", ["", " ", "abc", "twelve", "1a", "a1", "1.5",
                                     "0x10", "1 2", "+", "-", "--3", "1e3", "٣"])
    def test_garbage_is_rejected_not_zero(self, raw):
        assert parse_long(raw) is None

    @pytest.mark.parametrize("raw,want", [("0", 0), ("00", 0), ("7", 7), ("25", 25),
                                          ("+25", 25), ("-1", -1), ("-255", -255),
                                          ("255", 255), ("600000", 600000)])
    def test_well_formed_numbers_parse(self, raw, want):
        assert parse_long(raw) == want

    def test_absurdly_long_input_is_refused(self):
        assert parse_long("9" * 12) is None
        assert parse_long("99999999") is None      # > 2_000_000 guard

    def test_round_trips_every_value_we_accept(self):
        for value in range(-2000, 2001):
            assert parse_long(str(value)) == value

    def test_missing_argument_keeps_the_default(self):
        assert arg_clamp({}, "speed", 0, 255, 200) == (True, 200)
        assert arg_range({}, "ms", 0, MS_MAX, 0) == (True, 0)

    def test_speed_is_clamped_because_a_neighbour_means_the_same_thing(self):
        assert arg_clamp({"speed": "300"}, "speed", 0, 255, 200) == (True, 255)
        assert arg_clamp({"speed": "-5"}, "speed", 0, 255, 200) == (True, 0)

    def test_malformed_speed_is_refused_rather_than_defaulted(self):
        ok, _ = arg_clamp({"speed": "fast"}, "speed", 0, 255, 200)
        assert ok is False

    def test_negative_ms_is_refused_not_clamped_to_no_timed_stop(self):
        """Clamping ms=-1 to 0 would mean "never stop" — the opposite intent."""
        assert arg_range({"ms": "-1"}, "ms", 0, MS_MAX, 0) == (False, 0)
        assert arg_range({"ms": "nope"}, "ms", 0, MS_MAX, 0) == (False, 0)
        assert arg_range({"ms": str(MS_MAX + 1)}, "ms", 0, MS_MAX, 0) == (False, 0)

    def test_ms_within_range_is_accepted_exactly(self):
        for value in (0, 1, 1200, MS_MAX):
            assert arg_range({"ms": str(value)}, "ms", 0, MS_MAX, 0) == (True, value)

    @pytest.mark.parametrize("raw,want", [("1", True), ("0", False), ("2", True),
                                          ("true", True), ("false", False),
                                          ("on", True), ("off", False),
                                          ("yes", True), ("no", False)])
    def test_booleans_accept_words_and_numbers(self, raw, want):
        assert arg_bool({"swap_sides": raw}, "swap_sides", False) == (True, want)

    def test_malformed_boolean_is_refused(self):
        ok, _ = arg_bool({"swap_sides": "maybe"}, "swap_sides", False)
        assert ok is False


# ---------------------------------------------------------------------------
# Pin validation
# ---------------------------------------------------------------------------

FLASH_PINS = set(range(6, 12))          # SPI flash: using them crashes the board
ABSENT_PINS = {20, 24, 28, 29, 30, 31}  # not bonded out on the ESP32 package
INPUT_ONLY_PINS = set(range(34, 40))    # physically cannot drive anything
UART0_PINS = {1, 3}                     # the serial monitor
STRAPPING_PINS = {0, 2, 12, 15}         # read by the bootloader; allowed, warned


def pin_usable(p: int) -> bool:
    """firmware: pinUsable()"""
    if p < 0 or p > 33:
        return False
    if 6 <= p <= 11:
        return False
    if p in (20, 24):
        return False
    if 28 <= p <= 31:
        return False
    if p in (1, 3):
        return False
    return True


def pin_risky(p: int) -> bool:
    """firmware: pinRisky()"""
    return p in STRAPPING_PINS


@dataclass(frozen=True)
class Pins:
    a_r: int = 25
    a_l: int = 26
    a_en: int = 27
    b_r: int = 32
    b_l: int = 33
    b_en: int = 14


def pin_conflict(pins: Pins) -> int:
    """firmware: pinConflict() -> the clashing GPIO, or -1."""
    pwm = [pins.a_r, pins.a_l, pins.b_r, pins.b_l]
    for i in range(4):
        for j in range(i + 1, 4):
            if pwm[i] == pwm[j]:
                return pwm[i]
    for value in pwm:
        if value in (pins.a_en, pins.b_en):
            return value
    return -1


class TestPinValidation:
    """A GPIO that cannot drive an output must be refused, not attached.

    ``ledcAttach()`` on an absent or input-only pin fails silently. The
    firmware then reports the new configuration as live while one whole side
    is electrically disconnected — indistinguishable from the wiring fault
    this firmware exists to diagnose.
    """

    def test_pins_that_cannot_work_are_refused(self):
        for p in FLASH_PINS | ABSENT_PINS | INPUT_ONLY_PINS | UART0_PINS:
            assert not pin_usable(p), f"GPIO {p} must be refused"

    def test_out_of_range_is_refused(self):
        for p in (-1, -100, 40, 48, 999):
            assert not pin_usable(p)

    def test_the_usable_set_is_exactly_what_we_expect(self):
        usable = {p for p in range(-5, 50) if pin_usable(p)}
        expected = ({0, 2, 4, 5} | {12, 13, 14, 15, 16, 17, 18, 19}
                    | {21, 22, 23, 25, 26, 27} | {32, 33})
        assert usable == expected

    def test_every_default_pin_is_usable(self):
        pins = Pins()
        for p in (pins.a_r, pins.a_l, pins.a_en, pins.b_r, pins.b_l, pins.b_en):
            assert pin_usable(p)

    def test_defaults_do_not_use_a_bootloader_strapping_pin(self):
        pins = Pins()
        assert not any(pin_risky(p) for p in
                       (pins.a_r, pins.a_l, pins.a_en, pins.b_r, pins.b_l, pins.b_en))

    def test_strapping_pins_are_allowed_but_flagged(self):
        for p in STRAPPING_PINS:
            assert pin_usable(p), "someone short of pins may still need these"
            assert pin_risky(p), "...but they must be warned about it"

    def test_defaults_have_no_conflict(self):
        assert pin_conflict(Pins()) == -1

    def test_two_pwm_signals_cannot_share_a_gpio(self):
        assert pin_conflict(Pins(a_r=26, a_l=26)) == 26       # direction meaningless
        assert pin_conflict(Pins(a_r=32)) == 32               # A and B fighting
        assert pin_conflict(Pins(b_l=25)) == 25

    def test_a_pwm_pin_cannot_double_as_an_enable(self):
        assert pin_conflict(Pins(a_en=25)) == 25
        assert pin_conflict(Pins(b_en=33)) == 33

    def test_both_modules_may_share_one_enable_gpio(self):
        """The common wiring ties every R_EN/L_EN together — that is legal."""
        assert pin_conflict(Pins(a_en=27, b_en=27)) == -1

    def test_conflict_detection_is_exhaustive_over_small_sets(self):
        candidates = [4, 5, 13, 14]
        for combo in itertools.product(candidates, repeat=4):
            pins = Pins(a_r=combo[0], a_l=combo[1], b_r=combo[2], b_l=combo[3],
                        a_en=27, b_en=27)
            pwm = list(combo)
            expected_clash = len(set(pwm)) != len(pwm)
            assert (pin_conflict(pins) >= 0) is expected_clash


# ---------------------------------------------------------------------------
# Bridge enable state: coast must really coast
# ---------------------------------------------------------------------------


@dataclass
class Bridges:
    """The hardware state the firmware holds, as the BTS7960 sees it.

    The two enables are tracked separately because the firmware drives them
    separately: enabling both because one side is driving low-side-shorts the
    idle one.
    """

    en_a: bool = False
    en_b: bool = False
    live_a: int = 0
    live_b: int = 0
    target_a: int = 0
    target_b: int = 0
    braking_a: bool = False
    braking_b: bool = False
    wrote_brake_a: bool = False
    wrote_brake_b: bool = False

    @property
    def enables(self) -> bool:
        """True while either bridge is live."""
        return self.en_a or self.en_b


def side_effect(enable: bool, live: int, braking: bool) -> str:
    """What one module physically does, from what the firmware wrote to it.

    A BTS7960 with EN high and both inputs low turns its LOW-side FETs on and
    shorts the motor. That is a brake, and it is what "0 duty" looks like
    unless that side's enable is dropped.
    """
    if not enable:
        return "coast"
    rpwm, lpwm = write_side(live, braking)
    if rpwm == lpwm:                # both low (short) or both high (brake)
        return "brake"
    return "drive"


def sync_enables(state: Bridges) -> None:
    """firmware: syncEnables(). A side is live while it has something to do."""
    state.en_a = bool(state.target_a or state.live_a or state.braking_a)
    state.en_b = bool(state.target_b or state.live_b or state.braking_b)


def ramp_tick_bridges(state: Bridges, cfg: Config, dt_ms: int = 10) -> None:
    """firmware: rampTick(), including the symmetric enable release."""
    step = ramp_step(cfg, dt_ms)
    changed = (state.braking_a != state.wrote_brake_a or
               state.braking_b != state.wrote_brake_b)
    if state.live_a != state.target_a:
        state.live_a = ramp_tick(state.live_a, state.target_a, step)
        changed = True
    if state.live_b != state.target_b:
        state.live_b = ramp_tick(state.live_b, state.target_b, step)
        changed = True
    if changed:
        # Raise before writing duty; the release below happens after.
        state.en_a = state.en_a or bool(state.target_a) or state.braking_a
        state.en_b = state.en_b or bool(state.target_b) or state.braking_b
        state.wrote_brake_a = state.braking_a
        state.wrote_brake_b = state.braking_b
    sync_enables(state)


def settle(state: Bridges, cfg: Config, ticks: int = 400) -> Bridges:
    for _ in range(ticks):
        ramp_tick_bridges(state, cfg)
    return state


def request(state: Bridges, cfg: Config, left: int, right: int) -> Bridges:
    """firmware: applyLogical() as a drive command arriving over HTTP."""
    state.target_a, state.target_b = apply_logical(cfg, left, right)
    state.braking_a = state.braking_b = False
    sync_enables(state)
    return state


def raw_side_test(state: Bridges, side: str, signed_duty: int) -> Bridges:
    """firmware: handleTest(). Calibration is deliberately bypassed."""
    state.target_a = signed_duty if side == "a" else 0
    state.target_b = signed_duty if side == "b" else 0
    state.braking_a = state.braking_b = False
    sync_enables(state)
    return state


class TestEnableRelease:
    """A request for zero must coast, not latch a brake.

    ``rampTick()`` only ever RAISED the enables. A joystick returning to
    centre, ``speed=0``, or ``trim=0`` therefore left both bridges enabled
    with both inputs low — four motors shorted through the low-side FETs,
    reported as ``moving: false``, and unreachable by the failsafe (which
    only looks at moving robots) or the auto-stop (no ``ms`` pending).
    """

    def test_zero_valued_command_ends_up_coasting(self):
        cfg = Config()
        state = settle(request(Bridges(), cfg, 200, 200), cfg)
        assert side_effect(state.en_a, state.live_a, state.braking_a) == "drive"

        settle(request(state, cfg, 0, 0), cfg)
        assert state.enables is False
        assert side_effect(state.en_a, state.live_a, state.braking_a) == "coast"
        assert side_effect(state.en_b, state.live_b, state.braking_b) == "coast"

    def test_joystick_returning_to_centre_does_not_lock_the_wheels(self):
        cfg = Config()
        state = settle(request(Bridges(), cfg, 255, -255), cfg)
        settle(request(state, cfg, 0, 0), cfg)          # stick released
        assert state.enables is False

    def test_speed_zero_never_energises_the_bridges(self):
        cfg = Config()
        state = settle(request(Bridges(), cfg, 0, 0), cfg)
        assert state.enables is False

    def test_trim_zero_on_one_side_coasts_that_side_while_the_other_drives(self):
        """trim_a=0 makes side A's request 0 while side B drives normally."""
        cfg = Config(trim_a=0)
        state = settle(request(Bridges(), cfg, 200, 200), cfg)
        assert state.target_a == 0 and state.target_b == 200
        assert side_effect(state.en_b, state.live_b, state.braking_b) == "drive"
        assert side_effect(state.en_a, state.live_a, state.braking_a) == "coast"

    def test_a_raw_side_test_leaves_the_other_side_free_wheeling(self):
        """The one diagnostic that must show a single module in isolation.

        Enabling both bridges locked the untested side's wheels, so the robot
        skidded or pivoted instead of showing plainly which module responded.
        """
        cfg = Config()
        state = settle(raw_side_test(Bridges(), "a", 200), cfg)
        assert side_effect(state.en_a, state.live_a, state.braking_a) == "drive"
        assert side_effect(state.en_b, state.live_b, state.braking_b) == "coast"

        settle(raw_side_test(state, "b", -200), cfg)
        assert side_effect(state.en_b, state.live_b, state.braking_b) == "drive"
        assert side_effect(state.en_a, state.live_a, state.braking_a) == "coast"

    def test_a_one_sided_turn_coasts_the_idle_side(self):
        """left=200,right=0 should pivot, not brake the right wheels."""
        cfg = Config()
        state = settle(request(Bridges(), cfg, 200, 0), cfg)
        assert side_effect(state.en_a, state.live_a, state.braking_a) == "drive"
        assert side_effect(state.en_b, state.live_b, state.braking_b) == "coast"

    def test_ramp_down_keeps_the_bridges_live_until_it_reaches_zero(self):
        cfg = Config(ramp_ms=180)
        state = settle(request(Bridges(), cfg, 255, 255), cfg)
        request(state, cfg, 0, 0)
        ramp_tick_bridges(state, cfg)
        assert state.live_a != 0, "the ramp must still be running"
        assert state.en_a is True, "cutting mid-ramp would be a hard stop"
        settle(state, cfg)
        assert state.enables is False

    def test_an_explicit_brake_is_not_released(self):
        cfg = Config()
        state = Bridges(braking_a=True, braking_b=True, en_a=True, en_b=True)
        settle(state, cfg)
        assert state.en_a is True and state.en_b is True
        assert side_effect(state.en_a, state.live_a, state.braking_a) == "brake"
        assert side_effect(state.en_b, state.live_b, state.braking_b) == "brake"

    def test_release_is_idempotent_and_never_thrashes(self):
        cfg = Config()
        state = Bridges()
        for _ in range(50):
            ramp_tick_bridges(state, cfg)
            assert state.enables is False

    def test_every_zero_request_settles_to_coast(self):
        """Exhaustive over the calibration space: no setting can latch a brake."""
        for cfg in ALL_CALIBRATIONS:
            for trim_a, trim_b in itertools.product((0, 50, 100), repeat=2):
                for min_duty in (0, 60, MIN_DUTY_MAX):
                    tuned = replace(cfg, trim_a=trim_a, trim_b=trim_b,
                                    min_duty=min_duty)
                    state = settle(request(Bridges(), tuned, 200, -200), tuned)
                    settle(request(state, tuned, 0, 0), tuned)
                    assert state.enables is False, f"{tuned} latched a brake"


# ---------------------------------------------------------------------------
# Handler ordering: a rejected request must change nothing
# ---------------------------------------------------------------------------


@dataclass
class Machine:
    """The slice of firmware state an HTTP handler can mutate."""

    self_step: int = -1
    target_a: int = 0
    target_b: int = 0
    auto_stop_at: int = 0
    last_command: str = "stop"


def handle_motor(machine: Machine, args: dict, cfg: Config,
                 now: int = 1000) -> int:
    """firmware: handleMotor() after the validate-before-mutate fix."""
    direction = args.get("dir", "stop").lower()
    ok, speed = arg_clamp(args, "speed", 0, PWM_DUTY_MAX, cfg.default_speed)
    if not ok:
        return 400
    ok, ms = arg_range(args, "ms", 0, MS_MAX, 0)
    if not ok:
        return 400

    is_stop, is_brake = direction == "stop", direction == "brake"
    pair = None
    if not is_stop and not is_brake:
        pair = direction_to_pair(direction, speed)
        if pair is None:
            return 400                      # nothing has been touched yet

    machine.self_step = -1                  # an accepted command wins
    if is_stop or is_brake:
        machine.target_a = machine.target_b = 0
        machine.auto_stop_at = 0
        machine.last_command = "brake" if is_brake else "stop"
        return 200

    machine.target_a, machine.target_b = apply_logical(cfg, *pair)
    machine.auto_stop_at = (now + ms) if ms > 0 else 0
    machine.last_command = direction
    return 200


def handle_motor_buggy(machine: Machine, args: dict, cfg: Config,
                       now: int = 1000) -> int:
    """The old ordering, kept to prove the test can tell the difference."""
    direction = args.get("dir", "stop").lower()
    speed = cfg.default_speed
    machine.self_step = -1                  # <- mutated before validation
    if direction in ("stop", "brake"):
        machine.target_a = machine.target_b = 0
        machine.auto_stop_at = 0
        return 200
    pair = direction_to_pair(direction, speed)
    if pair is None:
        return 400
    machine.target_a, machine.target_b = apply_logical(cfg, *pair)
    return 200


def spinning_selftest() -> Machine:
    """A self-test mid-step: motors turning, the sequence owns the stop."""
    return Machine(self_step=2, target_a=0, target_b=200, auto_stop_at=0,
                   last_command="selftest")


class TestRejectedRequestsChangeNothing:
    """A 400 must leave the machine exactly as it was.

    Cancelling a running self-test and only then noticing the direction was a
    typo switched off the state machine that owns the stop, while the motors
    kept their spin values and nothing had an auto-stop pending. The caller is
    told its command failed; the robot keeps going.
    """

    @pytest.mark.parametrize("bogus", ["fwd", "up", "FORWARDS", "", "spin", "l"])
    def test_a_typo_does_not_cancel_a_running_selftest(self, bogus):
        machine = spinning_selftest()
        code = handle_motor(machine, {"dir": bogus}, Config())
        assert code == 400
        assert machine.self_step == 2, "the self-test must still own the motors"
        assert (machine.target_a, machine.target_b) == (0, 200)
        assert machine.last_command == "selftest"

    def test_the_old_ordering_would_have_orphaned_the_motors(self):
        machine = spinning_selftest()
        assert handle_motor_buggy(machine, {"dir": "fwd"}, Config()) == 400
        assert machine.self_step == -1          # the bug: sequence disabled...
        assert machine.target_b == 200          # ...while the motors still spin

    def test_a_malformed_speed_also_changes_nothing(self):
        machine = spinning_selftest()
        assert handle_motor(machine, {"dir": "forward", "speed": "fast"},
                            Config()) == 400
        assert machine.self_step == 2
        assert (machine.target_a, machine.target_b) == (0, 200)

    def test_a_malformed_duration_also_changes_nothing(self):
        machine = spinning_selftest()
        assert handle_motor(machine, {"dir": "forward", "ms": "-1"},
                            Config()) == 400
        assert machine.self_step == 2

    def test_a_valid_command_does_take_over(self):
        machine = spinning_selftest()
        assert handle_motor(machine, {"dir": "forward", "speed": "150"},
                            Config()) == 200
        assert machine.self_step == -1
        assert (machine.target_a, machine.target_b) == (150, 150)

    def test_stop_takes_over_and_cancels_the_selftest(self):
        machine = spinning_selftest()
        assert handle_motor(machine, {"dir": "stop"}, Config()) == 200
        assert machine.self_step == -1
        assert (machine.target_a, machine.target_b) == (0, 0)

    def test_a_timed_move_schedules_its_own_stop(self):
        machine = Machine()
        assert handle_motor(machine, {"dir": "forward", "ms": "800"},
                            Config(), now=5000) == 200
        assert machine.auto_stop_at == 5800

    def test_no_duration_means_the_failsafe_is_the_only_backstop(self):
        machine = Machine()
        handle_motor(machine, {"dir": "forward"}, Config())
        assert machine.auto_stop_at == 0


# ---------------------------------------------------------------------------
# Stored-configuration clamps
# ---------------------------------------------------------------------------


def config_apply_clamps(values: dict) -> dict:
    """firmware: configApplyClamps(). NVS can hold anything at all."""
    out = dict(values)
    out["trim_a"] = min(100, out.get("trim_a", 100))
    out["trim_b"] = min(100, out.get("trim_b", 100))
    freq = out.get("pwm_freq", 20000)
    out["pwm_freq"] = 20000 if (freq < 100 or freq > 25000) else freq
    out["failsafe_ms"] = min(60000, out.get("failsafe_ms", 10000))
    out["ramp_ms"] = min(3000, out.get("ramp_ms", 180))
    out["min_duty"] = min(MIN_DUTY_MAX, out.get("min_duty", 0))
    return out


class TestStoredConfigurationClamps:
    """Flash can hold a value the hardware cannot honour.

    A hand-edited partition, a half-finished write or a field written by an
    older build all reach ``configLoad()``. A ``ramp_ms`` of 60000 or a
    ``min_duty`` of 255 makes a correctly wired robot look broken, so the
    clamps run on load as well as on entry.
    """

    def test_min_duty_cannot_reach_full_scale(self):
        """min_duty=255 made every nonzero request full throttle."""
        assert config_apply_clamps({"min_duty": 255})["min_duty"] == MIN_DUTY_MAX
        assert config_apply_clamps({"min_duty": 200})["min_duty"] == MIN_DUTY_MAX
        assert config_apply_clamps({"min_duty": 60})["min_duty"] == 60

    def test_a_capped_deadband_still_leaves_real_range_above_it(self):
        cfg = Config(min_duty=MIN_DUTY_MAX)
        for value in range(MIN_DUTY_MAX + 1, 256):
            a, _ = apply_logical(cfg, value, 0)
            assert a == value, "above the deadband the request must pass through"

    def test_a_capped_deadband_never_inverts_the_speed_ordering(self):
        cfg = Config(min_duty=MIN_DUTY_MAX)
        previous = 0
        for value in range(1, 256):
            a, _ = apply_logical(cfg, value, 0)
            assert a >= previous
            previous = a

    def test_absurd_ramp_and_failsafe_are_clamped(self):
        clamped = config_apply_clamps({"ramp_ms": 60000, "failsafe_ms": 65535})
        assert clamped["ramp_ms"] == 3000
        assert clamped["failsafe_ms"] == 60000

    def test_out_of_band_pwm_frequency_falls_back_to_the_default(self):
        for freq in (0, 1, 99, 25001, 65535):
            assert config_apply_clamps({"pwm_freq": freq})["pwm_freq"] == 20000
        for freq in (100, 1000, 20000, 25000):
            assert config_apply_clamps({"pwm_freq": freq})["pwm_freq"] == freq

    def test_clamping_is_idempotent(self):
        wild = {"trim_a": 255, "trim_b": 200, "pwm_freq": 90000,
                "failsafe_ms": 65535, "ramp_ms": 65535, "min_duty": 255}
        once = config_apply_clamps(wild)
        assert config_apply_clamps(once) == once

    def test_defaults_survive_clamping_unchanged(self):
        defaults = {"trim_a": 100, "trim_b": 100, "pwm_freq": 20000,
                    "failsafe_ms": 10000, "ramp_ms": 180, "min_duty": 0}
        assert config_apply_clamps(defaults) == defaults


# ---------------------------------------------------------------------------
# Deadline sentinel
# ---------------------------------------------------------------------------

UINT32 = 1 << 32


def deadline_from_now(now: int, ms: int) -> int:
    """firmware: deadlineFromNow(). 0 is the "nothing pending" sentinel."""
    value = (now + ms) % UINT32
    return value if value else 1


class TestDeadlineSentinel:
    """A pending stop must never be mistaken for no pending stop.

    ``autoStopAt`` uses 0 to mean "no timed stop". Once per ``millis()`` wrap
    a real deadline lands exactly on 0 and would cancel itself, leaving a
    timed move running until the failsafe noticed.
    """

    def test_never_returns_the_sentinel(self):
        assert deadline_from_now(UINT32 - 500, 500) == 1
        for ms in range(1, 2000):
            assert deadline_from_now(UINT32 - ms, ms) != 0

    def test_ordinary_deadlines_are_exact(self):
        assert deadline_from_now(5000, 800) == 5800
        assert deadline_from_now(0, 1200) == 1200

    def test_wrapping_deadlines_stay_correct_modulo_the_clock(self):
        assert deadline_from_now(UINT32 - 100, 300) == 200


# ---------------------------------------------------------------------------
# Dashboard dead-man's switch
# ---------------------------------------------------------------------------

PAGE_SOURCE = (pathlib.Path(__file__).resolve().parents[2] / "firmware" /
               "esp32-iris-node-bts7960" / "page.h").read_text(encoding="utf-8")


class TestDashboardDeadMansSwitch:
    """The web page must not be able to leave the robot driving.

    Its drive controls used to be fire-and-forget clicks: one press and the
    robot ran until the firmware failsafe expired, up to ten seconds later,
    with nobody holding anything. The page now drives only while a control is
    held. These checks pin the handlers that make that true — losing any one
    of them silently restores the old behaviour.
    """

    def test_arrow_keys_stop_when_released(self):
        assert "keyup" in PAGE_SOURCE
        assert "keydown" in PAGE_SOURCE

    def test_pointer_release_is_handled_every_way_a_press_can_end(self):
        for event in ("pointerdown", "pointerup", "pointerleave", "pointercancel"):
            assert event in PAGE_SOURCE, f"missing {event} handler"

    def test_losing_the_window_or_tab_also_releases(self):
        assert "blur" in PAGE_SOURCE
        assert "visibilitychange" in PAGE_SOURCE

    def test_drive_controls_are_hold_to_drive_not_click_to_drive(self):
        assert "data-h=" in PAGE_SOURCE
        assert 'onclick="go(' not in PAGE_SOURCE, "click-to-drive is back"

    def test_a_held_control_keeps_itself_alive(self):
        """Otherwise a hold longer than failsafe_ms stops on its own."""
        assert "setInterval" in PAGE_SOURCE

    def test_save_reports_the_firmware_answer_rather_than_assuming_success(self):
        assert "SAVE FAILED" in PAGE_SOURCE


def enable_pin_levels(pins: Pins, en_a: bool, en_b: bool) -> dict:
    """firmware: setEnables(). One GPIO may carry both enables."""
    if pins.a_en == pins.b_en:
        return {pins.a_en: en_a or en_b}
    return {pins.a_en: en_a, pins.b_en: en_b}


class TestSharedEnableGpio:
    """Both modules may have every R_EN/L_EN tied to one GPIO.

    Per-side enables then cannot be written independently: the shared pin has
    to be high whenever EITHER side needs its bridge, or driving side A would
    cut power to side B mid-command.
    """

    def test_separate_pins_are_driven_independently(self):
        pins = Pins(a_en=27, b_en=14)
        assert enable_pin_levels(pins, True, False) == {27: True, 14: False}
        assert enable_pin_levels(pins, False, True) == {27: False, 14: True}

    def test_a_shared_pin_is_high_whenever_either_side_needs_it(self):
        pins = Pins(a_en=27, b_en=27)
        assert enable_pin_levels(pins, True, False) == {27: True}
        assert enable_pin_levels(pins, False, True) == {27: True}
        assert enable_pin_levels(pins, True, True) == {27: True}
        assert enable_pin_levels(pins, False, False) == {27: False}

    def test_a_shared_pin_never_cuts_a_driving_side(self):
        """Exhaustive: no combination drops the pin while a side is live."""
        pins = Pins(a_en=27, b_en=27)
        for en_a, en_b in itertools.product([True, False], repeat=2):
            level = enable_pin_levels(pins, en_a, en_b)[27]
            assert level == (en_a or en_b)
            if en_a or en_b:
                assert level is True

    def test_sharing_is_only_a_conflict_between_pwm_pins(self):
        assert pin_conflict(Pins(a_en=27, b_en=27)) == -1
        assert pin_conflict(Pins(a_r=25, b_r=25)) == 25


class TestArgumentContract:
    """The documented contract for each argument, pinned case by case.

    Three behaviours are deliberately different from each other, and each
    difference matters: ``dir`` is case-insensitive (people type LEFT),
    ``speed`` is clamped (300 obviously means "as fast as you can"), and
    ``ms`` is refused when out of range (clamping -1 to 0 would silently turn
    a short move into an indefinite one).
    """

    @pytest.mark.parametrize("raw", ["FORWARD", "Forward", "BaCkWaRd", "LEFT",
                                     "Right", "STOP", "Brake"])
    def test_direction_is_case_insensitive(self, raw):
        assert handle_motor(Machine(), {"dir": raw}, Config()) == 200

    @pytest.mark.parametrize("raw,want", [("-1", 0), ("0", 0), ("77", 77),
                                          ("255", 255), ("300", 255),
                                          ("99999", 255)])
    def test_speed_is_clamped_and_the_command_still_runs(self, raw, want):
        machine = Machine()
        assert handle_motor(machine, {"dir": "forward", "speed": raw},
                            Config()) == 200
        assert machine.target_a == want

    @pytest.mark.parametrize("raw", ["-1", "-1000", "600001", "9999999"])
    def test_out_of_range_duration_is_refused(self, raw):
        machine = spinning_selftest()
        before = (machine.self_step, machine.target_a, machine.target_b)
        assert handle_motor(machine, {"dir": "forward", "ms": raw},
                            Config()) == 400
        assert (machine.self_step, machine.target_a, machine.target_b) == before
