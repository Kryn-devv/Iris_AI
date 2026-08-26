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
