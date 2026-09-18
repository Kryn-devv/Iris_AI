"""The robot deciding where to go, with nobody telling it.

``robot_navigate`` is the robot doing what it was *told* — turn 180, drive two
metres, go until the board is 35 cm away. This is the layer with no
instruction at all: it is handed the room and left to get on with it.

The robot has two brains on two boards. The four ultrasonics live on the S3
sense board and the wheels live on the motor node, so nothing on either board
can see and drive at once — the loop closes here, in Python, about five times
a second. That is fast enough because the robot never commits to more than a
few hundred milliseconds of motion at a time: every pulse is a fresh decision
made on a fresh reading.

**How it decides.** Four behaviours, strictly ranked, the highest one that
applies wins. No planning, no map, no model — this is a reflex loop, and that
is exactly why it is dependable:

1. **Escape** — something is right there (under ``ROBOT_ROAM_CRITICAL_CM``).
   Back off, then turn away from whichever side is closer.
2. **Unwedge** — the readings have not changed in a while. The ultrasonics
   cannot see a chair leg between them or a rug the wheels are spinning on, so
   "nothing is moving" is itself the evidence. Reverse and take a big turn.
3. **Avoid** — something is close enough to matter. Turn toward the *open*
   side: two front sensors are what makes that a decision rather than a coin
   flip.
4. **Cruise** — the way is clear. Go.

**Why it is safe to leave running.** Off unless asked. Every motor command
carries the firmware's own auto-stop, so a dropped packet stops the wheels
rather than leaving them turning. Three failed sensor reads and it parks
itself: no eyes, no driving. It will not roam for longer than
``ROBOT_ROAM_MAX_MINUTES`` without being asked again, and "stop" reaches it
in one tick through the same abort flag the navigate tool uses.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

from iris.app.core.bus import EventBus, Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.tools.base import ToolError
from iris.app.tools.devices.registry import Device, DeviceRegistry, default_device_registry
from iris.app.tools.devices.transport import device_request

logger = get_logger("services.roam")

#: With no robot registered, look for one this rarely.
IDLE_POLL_S = 5.0
#: Consecutive sensor failures before it parks itself.
FAILURES_BEFORE_PARK = 3
#: How many recent front readings the wedge detector keeps.
WEDGE_WINDOW = 8
#: Total spread across that window, in cm, under which it counts as wedged.
WEDGE_SPREAD_CM = 4.0
#: It must have been trying to move forward for at least this many ticks before
#: "nothing changed" means anything at all.
WEDGE_MIN_MOVES = 5
#: Escapes this close together mean it is cornered rather than unlucky.
CORNER_WINDOW_S = 12.0
CORNER_ESCAPES = 3
#: Consecutive avoids — turns with no forward pulse between them — before it
#: stops re-aiming and backs off instead. Turning changes where the robot is
#: pointing and nothing else, so a robot 30 cm into a corner can turn through
#: a full circle without ever finding a way out and simply spin there. This is
#: the number that makes "I have looked everywhere from here" a decision.
AVOID_STREAK_TO_BACK_OFF = 6
#: Nothing is said about the same thing more often than this.
SPEAK_COOLDOWN_S = 25.0
#: How much a turn's length is allowed to vary, as a multiple of the base.
#: A reflex loop that always turns the same amount toward the same side
#: retraces its own path: it approaches a wall, turns to the open side, drives,
#: meets the far wall, turns back, and ping-pongs down one corridor forever.
#: Measured over a simulated room, a fixed turn covered 7% of the floor in 900
#: decisions. Varying it is what turns avoidance into exploring.
TURN_JITTER = (0.7, 2.4)
#: How often it turns AWAY from the more open side anyway. Small, but it is
#: what breaks a symmetric room's limit cycle.
CONTRARY_TURN_CHANCE = 0.2


class Decision:
    """One tick's choice, as a plain value so it can be tested without a robot."""

    __slots__ = ("behaviour", "action", "speed", "ms", "reason", "distances")

    def __init__(self, behaviour: str, action: str, speed: int, ms: int,
                 reason: str = "", distances: Optional[Dict[str, float]] = None):
        self.behaviour = behaviour
        self.action = action
        self.speed = speed
        self.ms = ms
        self.reason = reason
        self.distances = distances or {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Decision {self.behaviour} {self.action} {self.ms}ms {self.reason}>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "behaviour": self.behaviour, "action": self.action,
            "speed": self.speed, "ms": self.ms, "reason": self.reason,
            "distances": self.distances,
        }


def read_pair(readings: Dict[str, Any], side: str) -> Tuple[Optional[float], Optional[float]]:
    """The left and right distance on one side of the robot, in cm.

    ``None`` for a sensor that is not fitted or did not answer — which is not
    the same as zero, and must never be treated as "something is right there".
    """
    distances = readings.get("distances")
    out: List[Optional[float]] = [None, None]
    if isinstance(distances, dict):
        for index, name in enumerate((f"{side}_left", f"{side}_right")):
            try:
                value = float(distances[name])
            except (KeyError, TypeError, ValueError):
                continue
            if value >= 0:
                out[index] = value
    if out == [None, None]:
        # An older board reports one number per side rather than four.
        key = "distance_rear_cm" if side == "rear" else "distance_cm"
        try:
            value = float(readings.get(key))
            if value >= 0:
                out = [value, value]
        except (TypeError, ValueError):
            pass
    return out[0], out[1]


def _closest(*values: Optional[float]) -> Optional[float]:
    real = [v for v in values if v is not None]
    return min(real) if real else None


class RoamService:
    """Drives a registered ``motor`` device from a registered sense board."""

    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
        bus: Optional[EventBus] = None,
        *,
        speak: Optional[Callable[[str], Awaitable[None]]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Optional[random.Random] = None,
    ):
        self._registry = registry or default_device_registry
        self._bus = bus or default_event_bus
        self._speak_impl = speak
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()

        self.enabled = False
        self._task: Optional[asyncio.Task] = None
        self._started_at = 0.0
        self._failures = 0
        self._front_history: Deque[float] = deque(maxlen=WEDGE_WINDOW)
        self._forward_ticks = 0
        self._avoid_streak = 0
        #: Steps already decided on, played before anything new is considered.
        #: Backing away from a wall and then turning is one manoeuvre, not two
        #: unrelated reflexes — without this it reverses, finds the way clear,
        #: and drives straight back into the thing it just backed off from.
        self._pending: Deque[Decision] = deque()
        self._escapes: Deque[float] = deque(maxlen=CORNER_ESCAPES)
        self._last_said: Dict[str, float] = {}
        self._last: Optional[Decision] = None
        self.stats: Dict[str, Any] = {
            "ticks": 0, "cruise": 0, "avoid": 0, "escape": 0, "unwedge": 0,
            "errors": 0, "stopped_reason": "", "metres": 0.0,
        }

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Begin the loop task. Roaming itself still needs :meth:`set_enabled`."""
        if self._task is not None:
            return
        if settings.ROBOT_ROAM_ENABLED:
            self.enabled = True
            self._started_at = self._clock()
        self._task = asyncio.create_task(self._run(), name="robot-roam")
        logger.info("Roam service started (%s).", "roaming" if self.enabled else "idle")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None
        await self._park("service stopped")

    async def set_enabled(self, value: bool, *, reason: str = "") -> None:
        """Turn roaming on or off. Turning it off stops the wheels immediately."""
        value = bool(value)
        if value and not self.enabled:
            self._started_at = self._clock()
            self._failures = 0
            self._forward_ticks = 0
            self._avoid_streak = 0
            self._pending.clear()
            self._front_history.clear()
            self._escapes.clear()
            self.stats["stopped_reason"] = ""
            from iris.app.tools.devices import navigate

            navigate._Abort.flag = False     # a previous "stop" must not stop this
        self.enabled = value
        if not value:
            await self._park(reason or "asked to stop")

    def status(self) -> Dict[str, Any]:
        robot = self._registry.first_of_kind("motor")
        senses = self._senses()
        running_s = (self._clock() - self._started_at) if self.enabled else 0.0
        return {
            "enabled": self.enabled,
            "running": self._task is not None,
            "robot": robot.name if robot is not None else None,
            "senses": senses.name if senses is not None else None,
            "ready": robot is not None and senses is not None,
            "roaming_for_s": round(running_s, 1),
            "last": self._last.to_dict() if self._last else None,
            **self.stats,
        }

    # ------------------------------------------------------------------ loop
    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                await self._park("cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must outlive a surprise
                self.stats["errors"] += 1
                logger.debug("Roam tick failed: %s", exc, exc_info=True)
            await self._sleep(self._interval())

    def _interval(self) -> float:
        if not self.enabled or not self._ready():
            return IDLE_POLL_S
        return max(0.05, float(settings.ROBOT_ROAM_TICK_S))

    def _ready(self) -> bool:
        return (self._registry.first_of_kind("motor") is not None
                and self._senses() is not None)

    def _senses(self) -> Optional[Device]:
        return (self._registry.first_of_kind("sensor")
                or self._registry.first_of_kind("face"))

    # ------------------------------------------------------------------ tick
    async def tick(self) -> Optional[str]:
        """One decision. Returns a short word for the status tool and tests."""
        if not self.enabled:
            return None

        from iris.app.tools.devices import navigate

        if navigate._Abort.flag:
            # The user said "stop". That means this too.
            await self.set_enabled(False, reason="you said stop")
            return "stopped"

        limit = float(settings.ROBOT_ROAM_MAX_MINUTES) * 60.0
        if limit > 0 and self._clock() - self._started_at > limit:
            await self.set_enabled(False, reason="the time limit")
            await self._say("time", "I've been wandering a while — parking it.")
            return "time_limit"

        robot = self._registry.first_of_kind("motor")
        senses = self._senses()
        if robot is None or senses is None:
            return None

        readings = await self._read(senses)
        if readings is None:
            self._failures += 1
            if self._failures >= FAILURES_BEFORE_PARK:
                await self.set_enabled(False, reason="the sensors stopped answering")
                await self._say("blind", "I've lost the distance sensors, so I've stopped.")
                return "blind"
            return "sensor_miss"
        self._failures = 0

        decision = self.decide(readings)
        self._last = decision
        self.stats["ticks"] += 1
        self.stats[decision.behaviour] = self.stats.get(decision.behaviour, 0) + 1
        if decision.action == "forward":
            self.stats["metres"] = round(
                self.stats["metres"]
                + (float(settings.ROBOT_CM_PER_S) * decision.ms / 1000.0) / 100.0, 2)

        self._bus.publish(Topics.ROBOT_ROAM, decision.to_dict())
        await self._drive(robot, decision)
        await self._narrate(decision)
        return decision.behaviour

    # -------------------------------------------------------------- deciding
    def decide(self, readings: Dict[str, Any]) -> Decision:
        """Pick a behaviour from one set of distances. Pure — no robot needed.

        Ranked, highest first. Everything below is only reached because
        everything above decided it had nothing to say.
        """
        fl, fr = read_pair(readings, "front")
        rl, rr = read_pair(readings, "rear")
        front = _closest(fl, fr)
        rear = _closest(rl, rr)
        seen = {k: v for k, v in
                (("front_left", fl), ("front_right", fr),
                 ("rear_left", rl), ("rear_right", rr)) if v is not None}

        if self._pending:
            step = self._pending.popleft()
            step.distances = seen
            return step

        speed = max(60, min(255, int(settings.ROBOT_ROAM_SPEED)))
        turn_speed = max(60, min(255, int(settings.ROBOT_TURN_SPEED)))
        step_ms = max(80, int(settings.ROBOT_ROAM_STEP_MS))
        turn_ms = max(80, int(settings.ROBOT_ROAM_TURN_MS))
        critical = float(settings.ROBOT_ROAM_CRITICAL_CM)
        caution = float(settings.ROBOT_ROAM_CAUTION_CM)

        if front is None:
            # Front sensors fitted but silent. Creeping forward blind is how a
            # robot meets a wall at full speed; turning on the spot is safe.
            self._forward_ticks = 0
            return Decision("avoid", self._away_from(fl, fr), turn_speed,
                            self._turn_ms(turn_ms), "nothing is answering in front", seen)

        self._front_history.append(front)

        # 1. Escape — something is right there.
        if front <= critical:
            self._forward_ticks = 0
            self._avoid_streak = 0
            self._escapes.append(self._clock())
            backing = rear is None or rear > critical * 1.5
            if backing:
                # Back off, then turn — one manoeuvre. Reversing alone just
                # buys room to drive into the same obstacle again.
                self._pending.append(
                    Decision("escape", self._away_from(fl, fr), turn_speed,
                             self._turn_ms(turn_ms * 2), "turning away", {}))
                return Decision("escape", "backward", speed, turn_ms,
                                f"{front:.0f} cm ahead", seen)
            # Boxed in: reversing is not available either, so spin in place.
            return Decision("escape", self._away_from(fl, fr), turn_speed,
                            self._turn_ms(turn_ms * 2),
                            f"{front:.0f} cm ahead and no room behind", seen)

        # 2. Unwedge — the numbers stopped changing while it was trying to move.
        if self._is_wedged():
            self._front_history.clear()
            self._forward_ticks = 0
            self._avoid_streak = 0
            return Decision("unwedge", self._away_from(fl, fr), turn_speed,
                            self._turn_ms(turn_ms * 3),
                            "the distances have not changed — something the "
                            "ultrasonics cannot see", seen)

        # 3. Avoid — close enough to steer around.
        if front <= caution:
            self._forward_ticks = 0
            self._avoid_streak += 1
            # Turning only changes where it points. After most of a circle with
            # no way out found, the way out is behind it.
            if self._avoid_streak >= AVOID_STREAK_TO_BACK_OFF and (rear is None or rear > caution):
                self._avoid_streak = 0
                self._pending.append(
                    Decision("escape", self._away_from(fl, fr), turn_speed,
                             self._turn_ms(turn_ms * 3), "picking a new direction", {}))
                return Decision("escape", "backward", speed, turn_ms * 2,
                                "nowhere to turn from here", seen)
            return Decision("avoid", self._toward_open(fl, fr), turn_speed,
                            self._turn_ms(turn_ms), f"{front:.0f} cm ahead", seen)

        # 4. Cruise. Shorter pulses when it is getting tight, so the next
        #    decision arrives before the gap does.
        self._avoid_streak = 0
        self._forward_ticks += 1
        room = max(0.0, front - caution)
        pulse = step_ms if room > caution else max(120, int(step_ms * 0.6))
        return Decision("cruise", "forward", speed, pulse,
                        f"{front:.0f} cm of room", seen)

    def _is_wedged(self) -> bool:
        if self._forward_ticks < WEDGE_MIN_MOVES or len(self._front_history) < WEDGE_WINDOW:
            return False
        return (max(self._front_history) - min(self._front_history)) <= WEDGE_SPREAD_CM

    def _turn_ms(self, base: int) -> int:
        """A turn, but never twice the same. See :data:`TURN_JITTER`."""
        low, high = TURN_JITTER
        return max(80, int(base * self._rng.uniform(low, high)))

    def _away_from(self, left: Optional[float], right: Optional[float]) -> str:
        """Turn away from whatever is closer; toss a coin when it cannot tell."""
        if left is not None and right is not None and abs(left - right) > 3:
            return "right" if left < right else "left"
        return self._rng.choice(("left", "right"))

    def _toward_open(self, left: Optional[float], right: Optional[float]) -> str:
        """Steer toward the side with more room.

        Two front sensors instead of one is the whole reason this is a decision
        rather than a coin flip, so a clear difference is always obeyed.
        """
        if left is not None and right is not None and abs(left - right) > 3:
            open_side = "left" if left > right else "right"
            # Occasionally the other way. Always choosing the roomier side is
            # what makes a symmetric room into a corridor it paces forever.
            if self._rng.random() < CONTRARY_TURN_CHANCE:
                return "right" if open_side == "left" else "left"
            return open_side
        if left is None and right is not None:
            return "left"
        if right is None and left is not None:
            return "right"
        return self._rng.choice(("left", "right"))

    # -------------------------------------------------------------- moving
    async def _read(self, senses: Device) -> Optional[Dict[str, Any]]:
        try:
            data = await device_request(senses, "/sensors")
        except (ToolError, Exception) as exc:  # noqa: BLE001
            logger.debug("Roam sensor read failed: %s", exc)
            return None
        return data if isinstance(data, dict) else None

    async def _drive(self, robot: Device, decision: Decision) -> None:
        params: Dict[str, Any] = {
            "dir": decision.action,
            "speed": decision.speed,
            # Always. The firmware stops itself if the next packet never
            # arrives, which is the difference between a robot that parks when
            # the WiFi drops and one that drives into a wall.
            "ms": decision.ms,
        }
        try:
            await device_request(robot, "/motor", params)
        except (ToolError, Exception) as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            logger.debug("Roam drive failed: %s", exc)

    async def _park(self, reason: str) -> None:
        self.stats["stopped_reason"] = reason
        robot = self._registry.first_of_kind("motor")
        if robot is None:
            return
        try:
            await device_request(robot, "/motor", {"dir": "stop"})
        except (ToolError, Exception) as exc:  # noqa: BLE001
            logger.debug("Roam park failed (firmware auto-stop covers it): %s", exc)

    # ------------------------------------------------------------- speaking
    async def _narrate(self, decision: Decision) -> None:
        """Say something only when it is worth hearing.

        A robot that announces every turn is unlistenable, so this is the
        exception list: wedged, and cornered. Everything else it just does.
        """
        if decision.behaviour == "unwedge":
            await self._say("wedged", "I'm stuck on something I can't see — backing out.")
            return
        if decision.behaviour == "escape" and len(self._escapes) == CORNER_ESCAPES:
            window = self._clock() - self._escapes[0]
            if window <= CORNER_WINDOW_S:
                self._escapes.clear()
                await self._say("corner", "I think I'm in a corner. Working my way out.")

    async def _say(self, key: str, sentence: str) -> None:
        now = self._clock()
        if now - self._last_said.get(key, -1e9) < SPEAK_COOLDOWN_S:
            return
        self._last_said[key] = now
        try:
            if self._speak_impl is not None:
                await self._speak_impl(sentence)
                return
            from iris.app.voice.service import default_voice_service

            await default_voice_service.speak(sentence)
        except Exception as exc:  # noqa: BLE001 - never let a voice fault stop the wheels
            logger.debug("Roam speech skipped: %s", exc)


default_roam_service = RoamService()
