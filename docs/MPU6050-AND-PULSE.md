# The IMU and the pulse sensor

Two sensors on one extra I2C bus: an **MPU6050** six-axis motion sensor, and a
**MAX30100** (or MAX30102) pulse oximeter.

They share the right eye's two wires. Three different addresses on one bus is
exactly what I2C is for, so nothing has to be unplugged to make room:

| Device | Address | Bus |
|---|---|---|
| OLED eye | `0x3C` | either |
| MAX30100 / MAX30102 | `0x57` | aux |
| MPU6050 | `0x68` | aux |

---

## Wiring

Both sensors, in parallel, to the same four pins:

```
           ESP32-S3
  VCC  ──  3V3          (NOT 5V — both parts are 3.3V)
  GND  ──  GND
  SDA  ──  GPIO 38
  SCL  ──  GPIO 39
```

The MPU6050's **AD0** pin left unconnected puts it at `0x68`, which is where
the firmware looks. Tie it to 3V3 for `0x69` only if something else has taken
that address.

### Where to mount the MPU6050

Flat, near the middle of the chassis, chip side up. Two things matter:

- **Level.** Pitch and roll are measured against gravity, so a board glued at
  an angle reports that angle forever.
- **Away from the motors.** Their magnets don't bother it, but their vibration
  does. A rigid mount on a rattling plate reads as constant motion and the
  "is it stuck?" check stops working.

Double-sided foam tape on the main plate is ideal — the foam damps exactly the
buzz you want gone.

---

## Turning it on

In `firmware/esp32-s3-iris-sensors/esp32-s3-iris-sensors.ino`:

```cpp
const bool IMU_FITTED     = false;   // MPU6050 — set true when one is wired
const bool VITALS_FITTED  = true;    // MAX30100/2
const int  PIN_AUX_SDA    = 38;
const int  PIN_AUX_SCL    = 39;

const uint32_t VITALS_FINGER_DC = 30000;   // see "it never sees my finger"
```

A sensor flagged `false` is never started and costs nothing. The IMU ships off
because it is the later of the two additions; flip it to `true` the day one is
on the bus. Flash, and the boot log says what answered:

```
  [aux] I2C on SDA 38 / SCL 39 at 400 kHz (shared with the right eye)
  [aux] MPU6050 ok (who_am_i 0x68), heading zeroed
  [aux] MAX30102 ok at 0x57 — pulse/SpO2 ready
  [aux] NOT a medical device. Trends only, never diagnosis.
```

If a sensor doesn't answer, the firmware scans the bus and prints every
address that does — which turns "it doesn't work" into either "`0x68` is there
but won't start" or "the bus is empty", and those have different fixes.

> **The bus drops to 400 kHz** when either sensor is fitted. That's the
> MPU6050's ceiling, and an OLED sharing the wires has to live within it. The
> eyes redraw slightly slower. Nothing else changes.

---

## What you get

### `GET /imu`

```json
{"fitted": true, "pitch": 1.2, "roll": -0.4, "heading": 91.4,
 "accel_g": 1.00, "tilted": false, "bumped": false, "still": true,
 "calibrated": true}
```

The four judgements are the point; the raw angles are there for anything that
wants them.

| Field | Means |
|---|---|
| `tilted` | Past 45° — on its side, or picked up. Stop driving. |
| `bumped` | A jolt in the last half second. It hit something. |
| `still` | Not moving, whatever the wheels were told to do. |
| `heading` | Degrees turned since the last zero, ±180. |

`heading` is why this sensor earns its place. A timed turn is a guess that a
low battery or a carpet turns into a wrong guess. A heading is a measurement —
you can command "90 degrees" and know when you're there.

It's a **gyro** heading, not a compass: it drifts a degree or two per minute.
Fine across the ten seconds a turn takes, useless as an absolute bearing. Zero
it when a manoeuvre starts:

```
GET /imu/zero        reset heading to 0
GET /imu/calibrate   relearn the gyro bias (keep it still, ~300 ms)
```

### `GET /vitals`

```json
{"fitted": true, "part": "MAX30102", "finger": true,
 "bpm": 74, "spo2": 96, "settled": true, "medical_grade": false}
```

`bpm` and `spo2` are `0` until enough beats agree — better to say "keep still"
than to publish a number made from two noisy intervals.

Both also appear inside `/sensors` and in the telemetry IRIS receives, so
anything already reading that gets them without changes.

---

## Talking to it

```
"is the robot still upright"
"what's my heart rate"
"read all sensors"
```

---

## ⚠️ About the pulse sensor

**It is not a medical device and nothing it reports is a diagnosis.**

It's a $2 optical sensor with no calibration, no certification and no clinical
validation. The heart rate is usually close. The SpO2 is an estimate from a
published curve fit — a real pulse oximeter is calibrated per unit against
arterial blood gas measurements, and this is not, so treat its number as a
trend and nothing more.

**Do not make a health decision from it.** If someone seems unwell, the answer
is a doctor, not a number off a breadboard.

IRIS says so every time she reports one, on purpose: a number said out loud in
a house gets repeated later without the context it was given in, and "ninety-
four percent oxygen" is the kind of sentence people act on.

### Using it

Rest a fingertip on the sensor **lightly** and hold still. Pressing hard
squeezes the blood out of the capillaries and there's nothing left to measure —
the commonest reason it reads nothing. Give it 10–15 seconds.

### If it never sees your finger

Open `/vitals` and look at two fields:

```json
{"finger": false, "ir_dc": 4210, "finger_threshold": 30000}
```

`ir_dc` is how much infrared is coming back. Read it twice — once with a finger
resting on the sensor, once with nothing there — and set `VITALS_FINGER_DC` in
the sketch to roughly halfway between:

```
  nothing on it:   ~4,000      →  VITALS_FINGER_DC = 30000
  finger on it:   ~55,000
```

The right number is different for every setup. A MAX30102 counts to 262143
where a MAX30100 stops at 65535, the LED current changes it, and so does whose
finger it is. That is why it is a constant in the sketch and not a number
buried in the driver.

If `ir_dc` barely moves when you touch it, the sensor is not seeing skin at
all — check it is the right way up and that nothing is between the LEDs and
your fingertip.

### If it never answers at `0x57`

The common purple breakout has its I2C pull-up resistors tied to its own 1.8 V
regulator instead of to VIN. On a 3.3 V bus that sometimes works and sometimes
doesn't. The fix is to lift those two resistors and fit 4.7 kΩ from SDA and SCL
to 3V3 instead.

### "MAX30100" that isn't

Modules sold as MAX30100 are very often **MAX30102** — a different die, a
different register map, a different FIFO layout. The firmware reads the part ID
and configures whichever it actually found, so either works and the boot log
tells you which one you bought.
