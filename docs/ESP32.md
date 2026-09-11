# Connecting ESP32 boards to IRIS

IRIS drives two ESP32 boards over your WiFi: the **robot base** (an ESP32 with
two BTS7960 motor drivers) and the **S3 board** (an ESP32-S3 with two OLED
eyes and the sensors — temperature, humidity, motion, gas, flame, light,
distance). Each board runs a tiny web server; IRIS calls it over HTTP on your
local network. Nothing leaves your LAN, and IRIS refuses to send device
commands to non-local addresses.

There are two ways to hook a board up. **Both can be mixed freely** — one
registry holds all your devices.

---

## Path A — flash the IRIS firmware (recommended)

Two sketches, one per board:

| Board | Sketch | Section |
|---|---|---|
| robot base, 2× BTS7960 | `firmware/esp32-iris-node-bts7960/` | [Robot with BTS7960 drivers](#robot-with-bts7960-drivers-2-boards-4-wheel-drive) |
| ESP32-S3 eyes + sensors | `firmware/esp32-s3-iris-sensors/` | [The S3 node](#the-s3-node--the-robots-face-and-senses) |

### 1. Prepare your uploader (once) — Arduino IDE **or** PlatformIO

**PlatformIO (VS Code)** — every firmware folder is already a ready
PlatformIO project (`platformio.ini` included, compile-verified):
1. In VS Code, install the **PlatformIO IDE** extension (square alien-head icon).
2. *File → Open Folder* → pick the firmware folder itself, e.g.
   `firmware/esp32-iris-node-bts7960` (the folder, not the repo root).
3. Wait for PlatformIO to finish "Configuring project" the first time
   (it downloads the ESP32 toolchain once — a few minutes).
4. Edit the WiFi name/password in the `.ino`, plug in the board, then click
   the **→ (Upload)** arrow in the blue status bar at the bottom.
5. Click the **plug icon (Serial Monitor)** in the same bar to read the IP.

**Arduino IDE** — alternative:
1. Install [Arduino IDE](https://www.arduino.cc/en/software).
2. *File → Preferences → Additional boards manager URLs*, add:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
3. *Tools → Board → Boards Manager* → install **esp32 by Espressif Systems**.

> Board won't show a COM port on Windows? Install the USB driver for your
> board's serial chip (CP210x or CH340 — printed on the chip near the USB
> port), then replug.

### 2. Configure the sketch
Each sketch has one CONFIG block at the top. On both boards the only lines
that *must* change are the WiFi name and password:

```cpp
const char* WIFI_SSID = "your wifi name";
const char* WIFI_PASS = "your wifi password";
```

Everything else — motor pins and directions on the robot, sensor pins and
OLED wiring on the S3 — has a default that matches the wiring in the sections
below, and the robot's pins are calibrated from its own web page rather than
by editing code. **Both boards need a 2.4 GHz network**; an ESP32 cannot see
a 5 GHz-only one, and a phone hotspot set to 5 GHz looks exactly like a wrong
password.

### 3. Wiring
The robot's motor wiring is on its own printable sheet,
**[`wiring-motors.html`](wiring-motors.html)** (open it in any browser), and
the S3's sensor and OLED pins are in [the S3 section](#sensor-pins--change-them-in-config)
below. Two rules apply to both boards:

- **One shared ground.** Every GND — battery, drivers, sensors, both ESP32s —
  meets at one point. A sensor with its own floating ground reads garbage.
- **Nothing that draws real current comes off an ESP32 pin.** The pins carry
  *signals*. The motors take their power from the battery through the
  drivers, the sensors and OLEDs from the buck converter. The ESP32's 3.3 V
  pin can feed a driver's logic side and nothing more.

### 4. Flash and find the IP
Select your board (*Tools → Board → ESP32 Dev Module* for the robot,
*ESP32S3 Dev Module* for the S3), the right COM port, and Upload. Open
**Serial Monitor at 115200** — on connect the robot prints:

```
=================================
  IRIS robot (BTS7960 x2) online:  http://192.168.1.74
  Calibrate:        open that address in a browser
  Register in IRIS: add device robot at 192.168.1.74 as motor
  Fast path:        UDP 8267, control socket ws://192.168.1.74:81
=================================
```

and the S3 prints the same shape with `IRIS S3 node online` and `as face`.
Each board also serves its own control page at that IP, so you can always
drive the robot or test the face from a phone browser directly.

### 5. Register it with IRIS
Say (or type) to IRIS — one line per board, exactly as the serial monitor
printed it:

```
add device robot at 192.168.1.74 as motor
add device face at 192.168.1.70 as face
```

Done. Now these work — by voice too:

```
robot forward                    robot aage
move the robot left              robot peeche
stop the robot                   robot ruko
what's the temperature           kitna garam hai
is there any motion              gas level
how far is the object            is there a fire
look happy                       look surprised
is the robot online              list my devices
```

> Tip: give your router a DHCP reservation for each board (or use the
> `http://<name>.local` mDNS address) so the IP never changes.

---

## Robot with BTS7960 drivers (2 boards, 4-wheel-drive)

For a skid-steer robot where **two BTS7960 modules** each drive one side's
motors, flash `firmware/esp32-iris-node-bts7960/`. The full wiring, with
every wire named and the one rule that saves a driver, is on
**[`wiring-motors.html`](wiring-motors.html)**.

### You only edit two lines

```cpp
const char* WIFI_SSID = "your wifi name";
const char* WIFI_PASS = "your wifi password";
```

Pins, which side is which, which way is forward, and per-side speed are all
**calibrated from the board's own web page and saved to flash** — no
re-wiring and no re-flashing to fix a robot that turns the wrong way.

### Wiring — per BTS7960 module

| BTS7960 pin | Connect to |
|---|---|
| RPWM | an ESP32 GPIO (PWM) |
| LPWM | an ESP32 GPIO (PWM) |
| R_EN **and** L_EN | **tied together**, to one more GPIO — **not** to 3.3V, see below |
| VCC | **5V — required.** The logic side *consumes* 5V, it does not make it. A module with VCC unconnected looks completely dead. |
| GND | its own thin wire to ESP32 GND. The heavy motor return is battery minus straight to B−, never through this. |
| R_IS / L_IS | leave unconnected. They are *outputs*, and they exceed 3.3V at a couple of amps — never onto an ADC pin. |
| B+ / B− | motor battery — never the ESP32's 5V pin |

> **Do not tie R_EN/L_EN to 3.3V**, even though it saves two wires and plenty of
> guides suggest it. The BTS7960's over-temperature and short-circuit shutdown
> **latches**, and the only thing that clears it is taking the enable low again — so
> with it strapped high, the first thermal trip lasts until you unplug the battery.
> You also lose coasting (every stop and every failsafe becomes locked wheels), and
> during a current-limit event the PWM inputs are ignored, so a stalled motor cannot
> be stopped at all.
>
> **Give each module its own enable GPIO.** Sharing one between them is supported by
> the firmware, and `/status` and `/test` now report `shared_enable` when you have
> done it — but it brakes the idle side during the single-side test, which is the one
> diagnostic whose whole job is to show each module alone.
>
> **The DevKit's own `EN` pin is the ESP32's reset, not the module's enable.** The
> module's enables go to GPIO 27 and GPIO 14.

There is a full visual version of all this — three drawings, the assembly order, and
a symptom table — in [`wiring-motors.html`](wiring-motors.html).

Default pins (changeable live from the page):

| Side | RPWM | LPWM | EN |
|---|---|---|---|
| A | GPIO 25 | GPIO 26 | GPIO 27 |
| B | GPIO 32 | GPIO 33 | GPIO 14 |

### Calibrate it (this is the part that fixes wrong directions)

Flash and read the address from Serial Monitor @115200, then **open it in a
browser**.

> **No router, or the WiFi name/password is wrong?** The board gives up after
> 25 seconds and starts **its own WiFi** instead: join `iris-robot` with the
> password `iriscalib` and open `http://192.168.4.1`. You can calibrate the
> whole robot on the bench this way, with no network at all — and it keeps
> retrying your router in the background, so it switches over by itself once
> the router is reachable.

The page walks three steps:

1. **Find your sides.** Press `A fwd` / `A rev` / `B fwd` / `B rev` and watch
   which wheels move. These bypass all calibration, so they show the raw
   hardware. If a module never responds to either of its buttons, that is
   wiring — check its **VCC has 5V** and its **R_EN+L_EN are tied to the EN
   pin**. (`run full self-test` cycles all six moves automatically.)
   If you tied *both* modules' enables to one GPIO — allowed, and a normal way
   to wire this — the page says so, because then the side not under test is
   braked rather than free-wheeling and will drag against the one that is.
   Give each module its own enable GPIO if you want a clean single-side test.
2. **Fix directions.** Press `forward`. Wrong? Flip **swap sides** /
   **invert A** / **invert B** until forward is forward and left is left.
   Exactly one combination is correct for any given wiring.
3. **Drive straight.** If it veers, trim the faster side down.

Press **SAVE** and it persists across reboots. The page will tell you if the
write failed rather than claiming success.

The drive controls are **hold-to-drive**: the robot moves only while a button
or an arrow key is held down, and stops the moment you let go. Space stops
too. Nothing on the page can walk away leaving the motors running.

Then in IRIS: `add device robot at <IP> as motor`, and
`robot forward` · `move the robot left` · `stop the robot` · `robot peeche`.

### How fast it responds, and why it used to be slow

The page shows a **round trip** figure in the status block — the real
measured time from pressing a key to the board answering. On a healthy link
it reads single-digit milliseconds.

It used to read hundreds of milliseconds, or stall for whole seconds, and the
reason was not the motors. Arduino's `WebServer` — what all these boards run
— serves **one client at a time**, and it holds a socket that has connected
but not yet sent its request for up to **five seconds**, accepting nothing
else in the meantime. Browsers open exactly such idle pre-connect sockets, so
having the calibration page open was enough to queue the next drive command
behind it. Every command also paid a fresh TCP handshake, because the server
answers `Connection: close`.

So the drive path no longer goes through it. The robot node listens two more
ways, both feeding the same handlers:

| | | |
|---|---|---|
| **UDP** | `8267` | Payload is exactly what would follow the host in a URL: `/motor?dir=forward&speed=200`. One datagram, no handshake, nothing to queue behind. This is what IRIS uses. |
| **WebSocket** | `81` | One persistent socket for the calibration page, which cannot speak UDP. `/status` is *pushed* ten times a second while the wheels turn, instead of polled. |
| **HTTP** | `80` | Everything below, unchanged, plus the page itself. |

Nothing you already have breaks: HTTP still answers every endpoint, so curl,
scripts and older IRIS releases keep working, and the page falls back to HTTP
if the socket cannot open (it will say `over plain HTTP` instead of `over the
control socket`).

IRIS finds the UDP port from the board's own `/status`, which advertises it
under `"fast"`. It never guesses a port — a board that does not advertise one
only ever gets HTTP — and an unanswered datagram falls back to HTTP for that
command rather than being lost, which is what makes it safe to save
calibration over.

The **ramp** slider (step 4 on the page) is the other half of the delay: it
is how long the robot takes to reach full speed from a standstill. A soft
start is what stops four motors browning out the board, so it defaults to
90 ms rather than 0. Drop it if your battery can take it, and watch for the
board resetting as you accelerate.

### Safety behaviour

- Motion stops automatically if no command arrives for 10 s (configurable),
  so a dropped link can never leave the motors running. This is the backstop
  that holds no matter which network the commands came in on.
- Motion stops immediately if the WiFi carrying commands drops.
- Speed ramps instead of stepping, so four motors starting at once cannot
  brown-out the board.
- "Stop" really coasts. A BTS7960 with its enable high and both inputs low
  shorts the motor through the low-side FETs — that is a brake, not a coast —
  so stopping *disables the bridge* rather than just writing zero duty.
- Each side's bridge is enabled only while that side has something to do, so
  a raw single-side test lets the other side free-wheel instead of dragging
  against it, and `left=200&right=0` pivots rather than braking.
- Every numeric argument is parsed strictly. `/motor?speed=fast` is answered
  with an error instead of being read as `speed=0`, and a rejected request
  changes nothing at all — it cannot cancel a running self-test and leave the
  motors turning with nothing left to stop them.
- Only GPIOs that can actually drive an output are accepted for a motor pin.
  The ones that cannot (6–11 flash, 20/24/28–31 absent, 34–39 input-only,
  1/3 serial) are refused rather than silently attached to nothing, and the
  bootloader strapping pins (0/2/12/15) are allowed but warned about.

### Extra endpoints (beyond what IRIS uses)

| Endpoint | Purpose |
|---|---|
| `/tank?left=-255..255&right=-255..255` | direct per-side control |
| `/drive?y=…&x=…` | arcade/joystick mixing |
| `/test?side=a\|b&dir=forward\|backward` | raw single-side test |
| `/selftest` | timed A/B sequence; poll `/status` |
| `/config?...` · `/save` · `/reset` | live calibration |

## Path B — keep your existing firmware (recommended if you already coded it)

**Do not reflash anything.** IRIS doesn't care what code is on the board —
only that it answers a plain HTTP GET request at some URL. If your ESP32s
already run their own web server (a "Friday"-style smart home system, motor
control, whatever), keep it exactly as it is.

### 1. Register each board with the IP you already have

```
add device robot at 192.168.1.41 as motor
add device room sensor at 192.168.1.42 as sensor
```

If your firmware has no `/status` endpoint (most custom sketches don't),
IRIS will say it "did not answer yet" — that's fine, it still registers.

### 2. Map each command to the real URL your firmware already answers — by voice, no file editing

```
map robot forward command to /move?dir=fwd
map robot stop command to /move?dir=stop
```

Whatever paths your sketch responds to — check your own Arduino code for
the exact strings passed to `server.on(...)`, or open `http://<ip>/` in a
browser and click around your existing control page to see the URLs it
calls (browser dev tools → Network tab shows every request).

### 3. Just talk normally — IRIS now calls YOUR firmware's real endpoints

```
robot forward                  ·  robot aage
stop the robot                 ·  robot ruko
```

No JSON, no reflashing. (Advanced: `devices.json` in the data directory
holds the same mapping if you ever want to edit it directly, but the voice
commands above do the same thing.) A one-off call that has no permanent
command name — "send /selftest to the robot" — has no fixed phrasing; the AI
side picks the `device_command` tool for it, so it needs an LLM key. With no
key, open the board's own page in a browser instead.

---

## The API (what IRIS calls)

Both boards answer these over plain HTTP; the robot also takes the drive
commands over UDP and a WebSocket (see *How fast it responds*).

| Board | Endpoint | Example | Meaning |
|---|---|---|---|
| both | `GET /status` | `/status` | JSON: name, kind, ip, rssi, what the board is doing |
| robot | `GET /motor` | `/motor?dir=forward&speed=200&ms=1500` | drive; `dir` is required; auto-stops after `ms` |
| robot | `GET /tank` | `/tank?left=150&right=-150` | each side −255…255 |
| robot | `GET /stop` | `/stop` | stop now |
| S3 | `GET /sensors` | `/sensors` | JSON: temperature, humidity, motion, gas, flame, light, distances |
| S3 | `GET /face` | `/face?emotion=happy` | show an expression; `/face/list` names them all |
| S3 | `GET /look` | `/look?x=-60&y=0` | move the gaze, −100…100 on each axis; `/blink?count=2` blinks |
| S3 | `GET /speak` | `/speak?ms=2000` | animate the mouth for that long (IRIS calls it while it talks) |

Timed moves auto-stop even if WiFi drops mid-command (the deadline runs on the
board), and both boards reconnect to WiFi by themselves.

## The S3 node — the robot's face and senses

One board does both jobs: two OLED eyes and all the sensors. Flash
`firmware/esp32-s3-iris-sensors/` on the S3 (board: **ESP32S3 Dev Module**).

> **IRIS itself does not run on the S3, and does not need to.** IRIS is a
> Python application — the agent loop, the LLM gateway, the voice pipeline —
> and it runs on your PC. The S3 has 512 KB of RAM; it is the robot's face and
> senses, not its brain. One brain, many bodies.

> **Phone hotspot is fine.** The PC running IRIS and both boards join the
> same hotspot; you open the board's IP in a browser like any other page.
> Set the hotspot to **2.4 GHz** (an ESP32 cannot see 5 GHz, and that looks
> exactly like a wrong password) and expect addresses like `192.168.43.x`
> (Android) or `172.20.10.x` (iPhone). `.local` names usually do not work on
> a hotspot — use the IP the serial monitor prints.
>
> *(Running IRIS on a VPS instead is possible but optional — see
> [CLOUD.md](CLOUD.md). Nothing here needs it.)*

### Sensor pins — the firmware ships wired the way the robot is

Four HC-SR04 distance sensors (two looking ahead, two behind), DHT22, PIR,
flame, MQ-2 gas, LDR, two OLED eyes. **Nothing to move, nothing to add:**
every pin below is the firmware's default, so you change only the two WiFi
lines.

| Sensor | Pin | What you get |
|---|---|---|
| HC-SR04 **front-left** TRIG / ECHO | GPIO 4 / 5 | distance |
| HC-SR04 **front-right** TRIG / ECHO | GPIO 6 / 7 | distance |
| HC-SR04 **rear-left** TRIG / ECHO | GPIO 8 / 9 | distance |
| HC-SR04 **rear-right** TRIG / ECHO | GPIO 10 / 11 | distance |
| DHT22 DATA | GPIO 12 | temperature, humidity |
| PIR HC-SR501 OUT | GPIO 13 | motion |
| Flame module **DO** | GPIO 14 | fire yes / no |
| Flame module AO | GPIO 15 | *ignored* (see below) |
| MQ-2 gas AO | GPIO 16 | *ignored* (see below) |
| MQ-2 gas **DO** | GPIO 17 | gas yes / no, from the knob on the module |
| LDR | GPIO 18 | *ignored* (see below) |
| Both OLEDs, SDA / SCL | GPIO 20 / 21 | eyes — both panels show the same eye |

**Power: one buck converter at 3.30 V, everything on it — and no resistors.**
Dividers are only ever needed when a sensor is fed 5 V and its output would
be 5 V too. At 3.3 V nothing on this robot can make more than 3.3 V, so there
is nothing to divide. Keep the buck at 3.30 V. The one thing to watch: some
HC-SR04 clones insist on 5 V and will read *no echo* at 3.3 V — the board's
page shows that within a minute. If yours do, the fix is 5 V for the
ultrasonics plus a 1k/2k pair on each ECHO (or 3.3 V-rated modules); until
then everything else works.

**Why three wires are "ignored".** GPIO 11–20 share their analog converter
with the WiFi radio, so they cannot read a voltage while WiFi is on — a fact
of the chip, not of this firmware. Your flame AO (15), MQ-2 AO (16) and LDR
(18) sit on such pins. The firmware leaves them alone and uses the modules'
**digital** outputs instead: fire yes/no and gas yes/no still work, you just
do not get a *level*. Want the numbers later? Move **one** wire and change
**one** line:

| For | Move | Then set |
|---|---|---|
| gas level (0–4095) | MQ-2 **AO** from 16 → **GPIO 2** | `PIN_GAS_ADC = 2` |
| light percent | LDR from 18 → **GPIO 1** | `PIN_LDR_ADC = 1` |

**Four ultrasonics fire one at a time and never block.** Firing two together
means each hears the other's ping, and the false echo looks exactly like a
broken sensor. The firmware fires one every 60 ms and times the echo with an
interrupt, so the eyes keep animating and a face command never waits behind a
distance measurement. `/sensors` reports `distances: {front_left, front_right,
rear_left, rear_right}` (`null` = no echo) plus `distance_cm` (nearest ahead)
and `distance_rear_cm` (nearest behind).

**The DHT is slow on purpose.** A DHT22 needs about two seconds between reads,
so climate is sampled every 2.5 s and the last good value is cached in
between. `DHT_KIND` is `DHT22` (white module); set `DHT11` for the blue one.

### The eyes — two 0.96" OLEDs on one pair of wires

Every SSD1306 module answers at I2C address **0x3C**. With both wired to the
same SDA/SCL the board cannot tell them apart, so they always show the **same
picture** — and two identical eyes are a perfectly good pair of eyes. That is
the firmware's default (`TWIN_PANELS = true`): both modules on **SDA 20 /
SCL 21**, nothing to move. The only expression you lose is the wink.

GPIO 19/20 are also the S3's native-USB data pins, and the USB port *owns*
them at boot — a bus scan there hears nothing, exactly as if no wire were
fitted. The firmware takes them back at start-up (you will see `[pins] GPIO
19/20 taken back from the USB port`); that means you **flash and monitor
through the UART/COM socket** and leave the USB socket empty. If the eyes stay
dark there anyway, move the two wires to **41 (SDA) / 42 (SCL)** — pins with
nothing else on them — and set `PIN_L_SDA = 41; PIN_L_SCL = 42`.

Want two *independent* eyes later (the wink, a lopsided confused face)? Move
the right module's two wires to **SDA 38 / SCL 39** (pins with nothing else on
them — 17 and 18 carry the MQ-2 DO and the LDR) and set `TWIN_PANELS = false`.
Any other two free pins work too: put the numbers in `PIN_R_SDA` / `PIN_R_SCL`. Or, if you have soldered one module's address jumper to
0x3D, keep them on one bus and set `SHARED_BUS = true`.

At boot the serial monitor tells you exactly what it found:

```
  [eyes] left OLED ok at 0x3C on SDA 20 / SCL 21 (800 kHz)
  [eyes] twin panels: both OLEDs on SDA 20 / SCL 21 show the same eye
```

or, when nothing answers, it scans the bus and lists every address it heard,
so "dark" becomes "the bus is empty — VCC/GND/SDA/SCL" or "0x3C is there but
will not initialise — a 1.3" SH1106 module, which this firmware does not
drive". If an eye is missing the page and `/status` say which.

### How fast it responds

IRIS sends every face command as one UDP datagram (port 8267) and the board
answers in the same loop pass — about a millisecond on the board, plus
whatever the WiFi adds (typically 5–20 ms on a hotspot). HTTP still works for
everything, so the browser page and `curl` need nothing new. The page shows
the count of fast commands handled.

### Register it

```
add device face at 192.168.1.70 as face
```

A `face` device answers sensor questions too, so that one line covers both.

### It expresses itself automatically

You do not have to command the eyes. Every time IRIS speaks, it reads its own
sentence and sets a matching expression, plus a syllable-paced bounce for
however long the sentence takes to say:

| IRIS says | the eyes |
|---|---|
| "Done! Your presentation is ready." | excited |
| "Sorry, I could not find that file." | sad |
| "Let me check the weather…" | thinking |
| "Hello! Good morning." | happy |
| "That is not allowed." | angry |
| wake word heard | listening |

It reads Hindi and Hinglish too ("ho gaya" → excited, "ruko, dekh raha hoon"
→ thinking). Turn it off with `FACE_AUTO_EXPRESSION=false` in `.env`.

Between sentences the face is still alive: it breathes, blinks at random
intervals (sometimes twice), glances around, and after three minutes of
silence it dozes off — and wakes on the next thing IRIS says.

### Or ask directly

```
look happy          ·  khush ho jao
look sad            ·  udaas
be angry            ·  gussa dikhao
wink                ·  aankh maaro
blink               ·  palak jhapkao
look left  /  look at me  /  eyes up
show me love        ·  be excited  ·  look confused  ·  be sleepy
```

All 14: `neutral`, `happy`, `excited`, `love`, `sad`, `angry`, `surprised`,
`sleepy`, `thinking`, `confused`, `listening`, `wink`, `suspicious`, `dizzy`.

### Ask about the sensors

```
is there a fire           ·  aag lagi hai kya
is there any motion       ·  koi hai kya
what's the gas level      ·  gas level kya hai
how far is the object     ·  kitna door hai
what's the temperature    ·  kitna garam hai  ·  temperature batao
what's the humidity       ·  nami kitni hai
check the sensors
```

With all four HC-SR04s fitted, "how far is the object" answers with one
phrase — *"40 cm ahead, 12 cm behind on the left"* — naming a side only when
the two sensors on that side disagree by more than 15 cm. With only front
sensors, it says *"nearest object 82 cm ahead"*.

Flame and gas do not wait to be asked: the board reports them the instant it
sees them, and IRIS says so out loud with the eyes going wide. Repeats are
suppressed for 90 seconds so a sensor flickering on its threshold cannot turn
into a voice that will not stop.

### Give it a microphone and a speaker

Optional, and off by default (all six voice pins are `-1`). Wire an
**INMP441** I2S microphone and a **MAX98357A** I2S amplifier, set the pins
(mic SCK 38, WS 39, SD 40; amp BCLK 41, LRC 42, DIN 21 are free), and you can
just talk to the robot. The details are in
**[CLOUD.md](CLOUD.md#6-wiring-the-microphone-and-speaker)**.

### Test it with no software at all

Open the board's address in a browser: a button for every expression, a
talking test, a gaze pad, and live sensor readings. If an OLED did not
respond it says which side — the usual cause is VCC/GND, or a loose SDA/SCL.

No router, or a wrong WiFi password? After 25 seconds the board serves its own
network: join **`iris-face`** with password **`iriscalib`** and open
`http://192.168.4.1`. The eyes animate while it is still trying to connect, so
a frozen face always means a real fault rather than a slow boot.

## The camera — a third body, two wires

An **ESP32-CAM** on the robot's head gives IRIS eyes that recognise you and
name what you hold up. It takes 5 V and ground from the same rail and joins the
same hotspot; nothing is wired to the S3. Setup, phrases and what to install
are in **[CAMERA.md](CAMERA.md)**. When it spots a person, the OLED eyes turn
toward them.

## One brain, two bodies

```
                 your PC (IRIS = the only brain: voice, AI, decisions)
                          │  WiFi / HTTP
                ┌─────────┴─────────┐
                ▼                   ▼
           ESP32-S3            ESP32 "robot"
           face + sensors      BTS7960 x2 motors
           (2 OLED eyes,       (esp32-iris-node-
            4 ultrasonic,       bts7960)
            DHT22/PIR/gas/
            flame/light)
```

If a board currently runs its **own** voice/AI code (mic + STT on the ESP):
remove it. Two listening brains fight over commands and the ESP's speech
recognition is far weaker than IRIS's. Keep the boards as simple HTTP bodies —
IRIS hears, thinks, and calls them.

## Many boards

Register as many as you want — each is just a name + IP:

```
add device robot at 192.168.1.74 as motor
add device face at 192.168.1.70 as face
add device garage sensor at 192.168.1.76 as sensor
```

`list my devices` shows them all; `check devices` pings every one.

## Troubleshooting

- **Upload fails: "Failed to connect to ESP32: No serial data received"** —
  the build and COM port are fine; the chip just didn't enter flash mode.
  Click Upload again and, while the terminal prints `Connecting....`, press
  and **hold the BOOT button** on the board until `Writing at 0x...` lines
  appear. Stronger version: hold BOOT, tap EN/RST once, keep holding BOOT.
  Still stuck? Disconnect the driver wiring for the first flash (a powered
  BTS7960 can back-feed pins and block boot mode), use a direct USB
  port and a known-good data cable, or add `upload_speed = 115200` under the
  env in `platformio.ini`.

- **"Could not reach the device"** — board and PC must be on the *same* WiFi
  network (not guest WiFi); check the IP in Serial Monitor; ping it from the PC.
- **Robot turns the wrong way** — open the robot's own page and use the
  *swap sides* / *invert* switches, then *save*. No re-wiring.
- **IP changes after reboot** — set a DHCP reservation in your router, or
  register the device with its `.local` name instead of the IP.
- **`.local` name not found on Windows** — install Apple Bonjour or just use the IP.
