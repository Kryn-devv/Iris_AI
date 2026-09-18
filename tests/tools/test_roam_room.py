"""Put the robot in a room and see whether it gets around it.

The unit tests check one decision at a time. They cannot see the failure that
actually matters, because that failure is a *pattern*: a reflex loop that
turns the same amount toward the same side every time paces one corridor
forever. The first version of this behaviour never hit anything and covered
**7%** of the floor in 900 decisions — safe, and useless.

So the room is simulated: rays from four sensor positions, a differential
drive, two obstacles and four walls. Nothing here is a mock of the robot's
brain — ``decide()`` is the real thing, and it does not know it is not
driving.
"""

from __future__ import annotations

import math
import random

import pytest

from iris.app.core.config import settings
from iris.app.services.roam import RoamService

ROOM_W, ROOM_H = 400.0, 300.0          # a 4m by 3m room, in centimetres
CELL = 40.0                            # coverage is counted in 40cm squares
OBSTACLES = [(150.0, 150.0, 30.0), (300.0, 80.0, 25.0)]   # x, y, radius
#: Where the ultrasonics point, relative to straight ahead.
SENSOR_ANGLES = {"front_left": -18, "front_right": 18, "rear_left": 162, "rear_right": 198}


def _blocked(x: float, y: float) -> bool:
    if not (0 < x < ROOM_W and 0 < y < ROOM_H):
        return True
    return any((x - ox) ** 2 + (y - oy) ** 2 < r * r for ox, oy, r in OBSTACLES)


def _ray(x: float, y: float, heading: float, offset: float) -> float:
    angle = math.radians(heading + offset)
    dx, dy = math.cos(angle), math.sin(angle)
    for step in range(1, 401, 2):
        if _blocked(x + dx * step, y + dy * step):
            return float(step)
    return 400.0


def _reachable_cells() -> int:
    return sum(
        1
        for col in range(int(ROOM_W // CELL))
        for row in range(int(ROOM_H // CELL))
        if not _blocked(col * CELL + CELL / 2, row * CELL + CELL / 2)
    )


def drive(seed: int, ticks: int = 900):
    """Run the real decide() around the room. Returns (coverage, bumps, mix)."""
    service = RoamService(rng=random.Random(seed))
    rng = random.Random(seed)
    x, y = rng.uniform(30, 120), rng.uniform(30, 120)
    heading = rng.uniform(0, 360)
    visited, bumps = set(), 0
    mix: dict = {}

    for _ in range(ticks):
        readings = {"distances": {
            name: _ray(x, y, heading, offset) for name, offset in SENSOR_ANGLES.items()
        }}
        decision = service.decide(readings)
        mix[decision.behaviour] = mix.get(decision.behaviour, 0) + 1
        seconds = decision.ms / 1000.0

        if decision.action in ("forward", "backward"):
            travel = settings.ROBOT_CM_PER_S * seconds * (1 if decision.action == "forward" else -0.8)
            nx = x + math.cos(math.radians(heading)) * travel
            ny = y + math.sin(math.radians(heading)) * travel
            if _blocked(nx, ny):
                bumps += 1          # it drove into something
            else:
                x, y = nx, ny
        else:
            spin = settings.ROBOT_DEG_PER_S * seconds
            heading += spin if decision.action == "left" else -spin

        visited.add((int(x // CELL), int(y // CELL)))

    return len(visited) / _reachable_cells(), bumps, mix


SEEDS = list(range(6))


class TestItDoesNotHitThings:
    """The property that matters most, because the robot is real."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_it_never_drives_into_anything(self, seed):
        _coverage, bumps, _mix = drive(seed)
        assert bumps == 0, f"seed {seed} drove into something {bumps} times"


class TestItActuallyExplores:
    """Not hitting things is easy. A robot that never moves manages it."""

    @pytest.mark.parametrize("seed", SEEDS)
    def test_it_gets_around_most_of_the_room(self, seed):
        coverage, _bumps, _mix = drive(seed)
        assert coverage > 0.6, (
            f"seed {seed} reached only {coverage:.0%} of the floor. The first "
            "version managed 7% without hitting anything — it paced one "
            "corridor, because every turn was the same size toward the same side."
        )

    @pytest.mark.parametrize("seed", SEEDS)
    def test_it_spends_more_time_going_than_turning_in_circles(self, seed):
        _coverage, _bumps, mix = drive(seed)
        assert mix.get("cruise", 0) > 100, (
            f"seed {seed} only drove forward {mix.get('cruise', 0)} times out of "
            f"900 decisions: {mix}"
        )

    def test_different_rooms_are_not_walked_identically(self):
        """Two runs from different starts should not produce one fixed route."""
        paths = {drive(seed)[2].get("cruise") for seed in SEEDS}
        assert len(paths) > 1, "every run behaved identically; the jitter is not working"
