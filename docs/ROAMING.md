# Roaming — the robot with nobody steering it

`robot_navigate` is the robot doing what it was told: turn 180, drive two
metres, go until the board is 35 cm away. Roaming is the layer with no
instruction at all. You say **"go explore"** and it works the room out itself.

```
you  : go explore
iris : Off I go. I'll explore for about 10 minutes and stop if I lose the sensors.
you  : stop
iris : Stopped. Wheels are parked.
```

## Where the loop lives, and why

The robot has two brains on two boards. The four ultrasonics are on the S3
sense board; the wheels are on the motor node. Neither board can see *and*
drive, so the loop closes in Python — read `/sensors`, decide, send `/motor` —
about four times a second.

That is fast enough because **the robot never commits to more than a few
hundred milliseconds of motion.** Every pulse is a fresh decision on a fresh
reading, so the worst a late packet can cost is one short step. The tick is
250 ms on purpose: the sense board fires its four ultrasonics in turn 60 ms
apart, so a full set of fresh numbers takes 240 ms, and ticking faster than
that just reads the same values twice.

## How it decides

Four behaviours, strictly ranked. The highest one that applies wins, and the
ones below are only reached because everything above had nothing to say. No
map, no plan, no model — and that is the point: it is the same reflex loop
every time, which is what makes it predictable enough to leave running.

| | Behaviour | When | What it does |
|---|---|---|---|
| 1 | **Escape** | under `ROBOT_ROAM_CRITICAL_CM` (22 cm) | Back off, *then* turn away from the closer side — one manoeuvre, because reversing alone just buys room to drive into the same thing again. Boxed in front and back, it spins in place instead. |
| 2 | **Unwedge** | the numbers stopped changing while it was trying to move | The ultrasonics cannot see a chair leg between them or a rug the wheels are spinning on. "Nothing is moving" is itself the evidence. |
| 3 | **Avoid** | under `ROBOT_ROAM_CAUTION_CM` (45 cm) | Turn toward the side with more room. Two front sensors instead of one is what makes this a decision rather than a coin flip. |
| 4 | **Cruise** | the way is clear | Go. Shorter pulses as the room tightens, so the next decision arrives before the gap does. |

### The two things that took a simulation to find

The first version never hit anything and was still useless. Driven around a
simulated 4 × 3 m room for 900 decisions, it covered **7% of the floor**.

**Turning does not create space.** A robot 30 cm into a corner can turn
through a full circle and find no way out, because every heading still has a
wall in front — so it spins there forever. Six consecutive turns with no
forward pulse between them is now read as *"I have looked everywhere from
here"*, and it backs off instead. That alone took it from 3.3 m of travel to
15.8 m.

**Always turning the same amount toward the roomier side traces the same
path.** Approach a wall, turn to the open side, drive, meet the far wall, turn
back — a corridor it paces forever. Turn lengths are now varied, and one turn
in five deliberately goes the *less* open way, which is what breaks a
symmetric room's cycle.

Together: **7% → 93% coverage, still zero collisions.** Both numbers are
regression-tested in `tests/tools/test_roam_room.py`, which drives the real
`decide()` around that room — nothing about the robot's brain is mocked there.

## Why it is safe to leave running

- **Off unless asked.** `ROBOT_ROAM_ENABLED` is `false`. A robot that starts
  moving when IRIS launches is a robot that drives off a desk while you are
  still reading the startup log.
- **Every command carries the firmware's auto-stop.** A dropped WiFi packet
  parks the wheels rather than leaving them turning.
- **No eyes, no driving.** Three failed sensor reads and it stops itself and
  says so. It will not start at all without a sense board registered.
- **"Stop" reaches it in one tick**, through the same abort flag the navigate
  tool uses — and stopping is deliberately *not* behind a stricter permission
  than starting, because a gate that can refuse to stop a moving robot is not
  a safety feature.
- **It parks itself** after `ROBOT_ROAM_MAX_MINUTES`.

## It talks only when it is worth hearing

A robot narrating every turn is unlistenable. It says something when it is
wedged, and when it has escaped three times in twelve seconds and is
evidently in a corner. Everything else it just does. Each line has a cooldown.

## Knobs

| Setting | Default | What it does |
|---|---|---|
| `ROBOT_ROAM_ENABLED` | `false` | Start roaming the moment IRIS boots. Leave it off. |
| `ROBOT_ROAM_SPEED` | `150` | Slower than `ROBOT_CRUISE_SPEED`: every extra cm/s is less time to notice the table leg |
| `ROBOT_ROAM_CRITICAL_CM` | `22` | Under this, stop going forward and get out |
| `ROBOT_ROAM_CAUTION_CM` | `45` | Under this, steer toward the open side |
| `ROBOT_ROAM_STEP_MS` | `400` | One forward pulse |
| `ROBOT_ROAM_TURN_MS` | `320` | One turn, before jitter |
| `ROBOT_ROAM_TICK_S` | `0.25` | Look and decide this often |
| `ROBOT_ROAM_MAX_MINUTES` | `10` | Park after this long |

## What to say

"go explore", "wander around", "wander around for 2 minutes", "move on your
own", "drive yourself", "khud se ghoomo", "ghoomo" — and "stop exploring",
"stop roaming", or just "stop". All deterministic rules in
`iris/app/nlu/rules.py`; the model is not in this path.

Asking **"what are you doing"** or **"are you still exploring"** reports the
live behaviour, the reason for it, and roughly how far it has gone.

## No firmware changes

Both boards already do their half. The motor node's `ms` auto-stop and the
sense board's `/sensors` round-robin are what this is built on — flash what
you already have.
