# Connecting ESP32 boards to IRIS

IRIS can command any ESP32 on your WiFi — relay boards for home automation
(lights, fans, sockets) and motor drivers for the robot base. Each board runs a
tiny web server; IRIS calls it over HTTP on your local network. Nothing leaves
your LAN, and IRIS refuses to send device commands to non-local addresses.

There are two ways to hook a board up. **Both can be mixed freely** — one
registry holds all your devices.

---

## Path A — flash the IRIS node firmware (recommended)

One universal sketch: `firmware/esp32-iris-node/esp32-iris-node.ino`.

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
Open the `.ino` and edit the CONFIG block:

| Setting | What to put |
|---|---|
| `WIFI_SSID` / `WIFI_PASS` | your WiFi name and password |
| `DEVICE_NAME` | e.g. `"kitchen-light"`, `"robot"` — also becomes `http://<name>.local` |
| `DEVICE_KIND` | `"relay"` for lights/fans/sockets, `"motor"` for the robot |
| `RELAY_PINS` | the GPIOs your relay module IN pins connect to (default 26, 27, 32, 33) |
| `RELAY_ACTIVE_LOW` | keep `true` for the common blue relay modules |
| `MOTORS_ENABLED` | `true` on the robot board (L298N pins are in the sketch) |
| `PIN_SERVO` | GPIO for the servo's **signal** wire (default 19); `-1` if no servo |
| `SERVO_POWER_CH` | relay channel that switches the servo's **+** (default 3); `0` if it is hard-wired to 5V |

### 3. Wiring (typical)
**Relay module:** ESP32 `5V/VIN → VCC`, `GND → GND`, `GPIO26 → IN1`,
`GPIO27 → IN2`, …

**A relay is a switch, nothing more.** Each channel has three screw terminals:
COM, NO and NC. The **+ wire coming from the power rail goes to COM**, the
**+ wire going on to the appliance goes to NO**, and the appliance's **− goes
straight to the shared ground — never through the relay.** NC is the terminal
that is connected when the channel is off; leave it empty for a normally-off
appliance. That is the whole of it: the relay interrupts the positive wire, and
the negative is always joined.

Because the relay only opens and closes a contact, it does not care what
voltage runs through it. Switching a 12V strip or a 5V servo needs no AC
anywhere in the build. If you *do* put mains through the COM/NO terminals,
that is a live-mains job with real shock risk — a low-voltage DC load is the
safer build and the one the rest of these docs assume.

**Servo:** the **signal** (orange/yellow) wire goes to `PIN_SERVO`. The
**power** (red) wire does **not** come from any ESP32 pin — the onboard
regulator cannot source a servo's stall current and trying it browns out the
board halfway through a move. Feed the red wire from the 5V rail through the
relay channel named in `SERVO_POWER_CH`, and the brown/black wire to the shared
ground.

That relay channel is what stops an idle servo buzzing. After each move the
firmware opens the channel, so the servo goes properly dead instead of fighting
its own gearbox and cooking itself holding position. Ask for `hold` when you
actually want it to keep pushing. On each command the pulse is set *before* the
channel closes, so the servo wakes up already knowing where to go.

**L298N motor driver:** `GPIO25 → ENA`, `GPIO13 → IN1`, `GPIO12 → IN2`,
`GPIO14 → ENB`, `GPIO21 → IN3`, `GPIO22 → IN4`, common GND between ESP32 and
driver, motor battery to the driver's 12V input.

> **Using BTS7960 instead of L298N?** (Common for a 4-wheel-drive robot with
> two driver boards — one per side.) Don't use this sketch's motor section.
> Flash **`firmware/esp32-iris-node-bts7960/esp32-iris-node-bts7960.ino`**
> instead — same `/motor` API, wired for two BTS7960 boards. See its own
> wiring table below.

### One 12V battery, three appliances — the whole power tree

> **There is a drawn version of everything below** in
> **[`wiring-12v.html`](wiring-12v.html)** — open it in any browser (no server
> needed) for the four diagrams: the power tree, what a relay channel actually
> is, all four channels at once, and the fan's flyback diode. Print it and take
> it to the bench.

This is the build most people end up with: a 12V battery, a 4-channel relay
module, and three things to switch — a **12V DC light**, a **3V DC fan** and a
**servo**.

**There is no AC anywhere in it.** Every load is 12V DC or lower, so nothing
here involves mains voltage, a plug, or live wiring. A relay *can* switch AC,
which is why every tutorial warns about it, but you are not using it that way.
The 12V battery is the only source in the system.

#### What you need beyond the parts you have

| Part | Why | Roughly |
|---|---|---|
| 2 × LM2596 buck converter (**3A**, adjustable) | the battery is 12V; the ESP32 and relay need 5V and the fan needs 3V | £2 each |
| Inline blade fuse holder + **5A** fuse | a pinched wire otherwise puts a battery's full short-circuit current into a spark | £1 |
| Rocker switch (rated 12V 5A+) | one thing that kills the whole system | £1 |
| 1 × **1N4007** diode | the fan is a motor; see the flyback note below | pennies |

Get **3A** buck modules, not the 2A ones. The reason is in the current budget
below.

#### The three rails

```
                 ┌─ 5A fuse ─ switch ─┬──────────────────────── 12V rail
  12V battery  + ┘                    ├─ buck #1 ─▶ 5.0V ────── 5V rail
               −  ───────────────┐    └─ buck #2 ─▶ 3.2V ────── 3V rail
                                 └────────────────────────────── GROUND
```

| Rail | Feeds |
|---|---|
| **12V** | relay CH1 COM (→ the 12V light) |
| **5V** | ESP32 `5V`/`VIN` pin · relay module `VCC` · relay CH3 COM (→ the servo) |
| **3V** | relay CH2 COM (→ the fan) |
| **GROUND** | battery − · both bucks' − out · ESP32 `GND` · relay `GND` · light − · fan − · servo brown — **all joined at one point** |

Every black wire in the build meets at that one point. A build where the
appliance grounds come back separately works by luck; a build with one star
ground works by design.

#### The relay channels

| CH | ESP32 pin → IN | COM ← from | NO → to | What it switches |
|---|---|---|---|---|
| 1 | GPIO 26 → IN1 | 12V rail | light **+** | the 12V light |
| 2 | GPIO 27 → IN2 | 3V rail | fan **+** | the 3V fan |
| 3 | GPIO 32 → IN3 | 5V rail | servo **red** | the servo's power |
| 4 | GPIO 33 → IN4 | — | — | spare |

Leave **NC** empty on all four. NC is the terminal that is connected when the
channel is *off*, which is not what you want for any of these.

The servo's **signal** wire (orange or yellow) does **not** go through the
relay — a relay cannot make a pulse. It goes straight from **GPIO 19** to the
servo. CH3 only decides whether the servo has power. Its brown/black wire goes
to the shared ground like everything else.

#### Assemble it in this order

The order matters more than the wiring does, because two of these steps are
where parts get destroyed.

1. Build the 12V side — battery lead, fuse holder, switch, and the two bucks'
   **inputs**. **Do not connect the battery yet.** Nothing on the bucks' outputs.
2. Battery on, switch on. Put a multimeter on **buck #1's output** and turn its
   little screw until it reads **5.0 V**. Switch off.
3. Same for **buck #2**, until it reads **3.2 V**. Switch off, battery off.
4. **Only now** connect the loads. A buck module out of the box can be set
   anywhere from 1.25V to nearly its input voltage — connecting the 3V fan
   before you have set that screw is how a 3V fan dies in one second.
5. Wire the relay COM/NO terminals, the four IN signal wires, the servo, and
   the ESP32's 5V and GND.
6. Battery on. The relay board's power LED lights, the ESP32 prints its IP,
   and every channel starts **off**.

#### Current budget — why 3A buck modules

| On the 5V rail | Draw |
|---|---|
| ESP32 with WiFi transmitting | ~250 mA in bursts |
| Relay module, all four coils closed | ~280 mA |
| SG90 servo, moving | ~400 mA |
| SG90 servo, stalled against a stop | ~700 mA |
| MG996R servo, stalled | up to 2.5 A |

A 2A module survives the typical case and browns out the ESP32 halfway through
a servo move — which reads as "the board keeps rebooting when the curtain
moves", not as a power problem. A 3A module has the headroom. The 12V light and
the 3V fan each sit on their own rail and are small (a 12V 5W strip is ~0.4 A,
a small fan 100–250 mA), so the 5A fuse covers the whole system comfortably
while still being far below what the wire can carry.

#### Three things worth knowing before you power it

**The fan needs a flyback diode.** A DC motor's coil field collapses when the
relay opens and drives a reverse voltage spike back down the wire. It arcs the
relay contacts and can reset the ESP32. Put the **1N4007 across the fan's own
two terminals, with the stripe (cathode) on the + side**. It does nothing at
all in normal running and absorbs the spike on switch-off.

**Nothing motorised comes off an ESP32 pin.** Not the fan, not the servo. The
3.3V regulator on the board cannot source a motor's current, and the 5V pin is
just the incoming supply passed through — hanging a servo on it drags the
board's own supply down with it. Both get their power from the 5V rail, through
the relay.

**A 3.3V GPIO driving a 5V relay module usually works, and sometimes doesn't.**
The relay's opto-input is designed around 5V logic. Most modules trigger fine
at 3.3V. If one channel never clicks while the others do, that is the cause,
not your wiring — the fix is a module labelled "3V3" or a 4-channel level
shifter, not more soldering.

### 4. Flash and find the IP
Select your board (*Tools → Board → ESP32 Dev Module*), the right COM port,
and Upload. Open **Serial Monitor at 115200** — on connect the board prints:

```
=================================
  IRIS node online:  http://192.168.1.73
  Register in IRIS:  add device kitchen-light at 192.168.1.73
=================================
```

It also serves its own control page at that IP (like your existing boards do),
so you can always drive it from a phone browser directly.

### 5. Register it with IRIS
Say (or type) to IRIS:

```
add device kitchen light at 192.168.1.73 as relay
add device robot at 192.168.1.74 as motor
```

Done. Now these work — by voice too:

```
turn on the kitchen light        light chalu karo
switch off the fan               fan band karo
toggle the socket                robot forward
move the robot left              stop the robot
is the light online              list my devices
open the curtain                 curtain kholo
close the curtain                parda kholo
open the curtain halfway         set the servo to 45 degrees
```

> Tip: give your router a DHCP reservation for each board (or use the
> `http://<name>.local` mDNS address) so the IP never changes.

---

## Robot with BTS7960 drivers (2 boards, 4-wheel-drive)

For a skid-steer robot where **two BTS7960 modules** each drive one side's
motors, flash `firmware/esp32-iris-node-bts7960/` — **not** the plain
`esp32-iris-node.ino` (that one is wired for an L298N and cannot drive a
BTS7960 correctly).

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
| R_EN **and** L_EN | **tied together**, to one more GPIO (or straight to 3.3V) |
| *(optional)* | both modules may share **one** enable GPIO — tie all four EN pins together |
| VCC | **5V — required.** The logic side *consumes* 5V, it does not make it. A module with VCC unconnected looks completely dead. |
| GND | ESP32 GND **and** battery minus — all grounds common |
| B+ / B− | motor battery — never the ESP32's 5V pin |

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
add device hall light at 192.168.1.40 as relay
add device robot at 192.168.1.41 as motor
add device room sensor at 192.168.1.42 as sensor
```

If your firmware has no `/status` endpoint (most custom sketches don't),
IRIS will say it "did not answer yet" — that's fine, it still registers.

### 2. Map each command to the real URL your firmware already answers — by voice, no file editing

```
map hall light on command to /relay1on
map hall light off command to /relay1off
map robot forward command to /move?dir=fwd
map robot stop command to /move?dir=stop
```

Whatever paths your sketch responds to — check your own Arduino code for
the exact strings passed to `server.on(...)`, or open `http://<ip>/` in a
browser and click around your existing control page to see the URLs it
calls (browser dev tools → Network tab shows every request).

### 3. Just talk normally — IRIS now calls YOUR firmware's real endpoints

```
turn on the hall light         ·  hall light chalu karo
robot forward                  ·  robot stop
```

No JSON, no reflashing. (Advanced: `devices.json` in the data directory
holds the same mapping if you ever want to edit it directly, but the voice
commands above do the same thing.) For a one-off call that has no permanent
command name:

```
device_command bedroom light /servo?angle=90
```

---

## The API (what IRIS calls)

| Endpoint | Example | Meaning |
|---|---|---|
| `GET /status` | `/status` | JSON: name, kind, ip, rssi, relay states |
| `GET /relay` | `/relay?ch=1&state=on` | channel 1 on / off / toggle |
| `GET /servo` | `/servo?angle=90` | point the servo; add `&hold=1` to keep it powered |
| `GET /motor` | `/motor?dir=forward&speed=200&ms=1500` | drive; auto-stops after `ms` |

Timed moves auto-stop even if WiFi drops mid-command (the deadline runs on the
board), and the board reconnects to WiFi by itself.

## The S3 node — the robot's face and senses

One board does both jobs: two OLED eyes and all the sensors. Flash
`firmware/esp32-s3-iris-sensors/` on the S3 (board: **ESP32S3 Dev Module**).

> **IRIS itself does not run on the S3, and does not need to.** IRIS is a
> Python application — the agent loop, the LLM gateway, the voice pipeline —
> and it runs on your PC. The S3 has 512 KB of RAM; it is the robot's face and
> senses, not its brain. One brain, many bodies.

> **Running IRIS on a VPS instead of your PC?** Then IRIS cannot call your
> boards — they are behind your router's NAT. They dial *out* to IRIS instead,
> over a WebSocket, with no port-forwarding and nothing exposed. That plus the
> microphone/speaker wiring, Pterodactyl deployment and the whole end-to-end
> mechanism is in **[CLOUD.md](CLOUD.md)**.

### Sensor pins — change them in CONFIG:

| Sensor | Pin | Note |
|---|---|---|
| PIR HC-SR501 OUT | GPIO 4 | 3.3V output, connect directly |
| MQ-2 gas AO | GPIO 5 | ⚠ through a 1k/2k voltage divider (AO can reach ~4V) |
| LDR divider midpoint | GPIO 6 | LDR + 10k resistor from 3.3V |
| HC-SR04 #1 (front) TRIG | GPIO 7 | direct |
| HC-SR04 #1 (front) ECHO | GPIO 8 | ⚠ through a 1k/2k divider (ECHO is 5V) |
| HC-SR04 #2 (rear) TRIG | GPIO 38 | direct |
| HC-SR04 #2 (rear) ECHO | GPIO 39 | ⚠ through a 1k/2k divider (ECHO is 5V) |
| DHT11/DHT22 DATA | GPIO 40 | direct. Module boards have the 10k pull-up already; a bare 4-pin sensor needs one 10k from DATA to 3.3V |
| Flame module DO | GPIO 13 | 3.3V output, direct. Use **DO**, not AO. Most modules are active-LOW — the default matches |

**The S3's pins are NOT 5V tolerant** — skipping the ECHO dividers can kill
inputs. Power PIR/MQ-2/HC-SR04 from the 5V pin, the LDR and the DHT from 3.3V.
Set any unused sensor's pin to `-1`.

**Two ultrasonics fire alternately, never together.** If both ping at the same
instant each one hears the other's burst, and the false echo looks exactly like
a broken sensor rather than like interference. The firmware reads one per slot
and alternates, so each still refreshes several times a second.

**The DHT is slow on purpose.** A DHT11 needs about a second between reads and
a DHT22 two, so climate is sampled every 2.5 s (`climateEveryMs`) and the last
good value is cached in between. Set `DHT_KIND` to `DHT11` (blue module) or
`DHT22` (white module) — the wrong one reads as `nan` and IRIS simply omits it
rather than reporting a made-up number.

Both extra sensors are optional: leave `PIN_US_TRIG2`/`PIN_US_ECHO2`/`PIN_DHT`
at `-1` and everything else keeps working.

**Analog sensors must be on GPIO 1–10.** GPIO 11–20 are ADC2, and ADC2 stops
working the moment WiFi comes up — the reading silently returns garbage. The
firmware prints a warning at boot if you have put one there.

### The eyes — two 0.96"/0.98" OLEDs

Almost every SSD1306 module is hard-wired to I2C address **0x3C**, and two
devices cannot share an address on one bus. Rather than make you solder the
address jumper, each eye gets **its own I2C bus** — the S3 has two:

| OLED pin | Left eye | Right eye |
|---|---|---|
| SDA | GPIO 9 | GPIO 11 |
| SCL | GPIO 10 | GPIO 12 |
| VCC | 3.3V | 3.3V |
| GND | GND | GND |

That is all. No jumpers, no soldering, no address changes.

*(If you have already moved one module to 0x3D, set `SHARED_BUS = true` and
wire both to the left-eye pins instead. If left and right come out reversed,
set `SWAP_EYES = true` — no rewiring.)*

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

With both HC-SR04s fitted, "how far is the object" answers with one phrase —
*"82 cm ahead, 15 cm behind"* — rather than two numbers you have to pair up
yourself. With only the front one, it says *"nearest object 82 cm away"*.

Flame and gas do not wait to be asked: the board reports them the instant it
sees them, and IRIS says so out loud with the eyes going wide. Repeats are
suppressed for 90 seconds so a sensor flickering on its threshold cannot turn
into a voice that will not stop.

### Give it a microphone and a speaker

Wire an **INMP441** I2S microphone and a **MAX98357A** I2S amplifier and you can
just talk to the robot: it uploads what you said, IRIS answers, and the reply
plays through the speaker while the eyes bounce along with it. Pins and the
tuning knobs are in **[CLOUD.md](CLOUD.md#6-wiring-the-microphone-and-speaker)**.

### Test it with no software at all

Open the board's address in a browser: a button for every expression, a
talking test, a gaze pad, and live sensor readings. If an OLED did not
respond it says so there — the usual cause is VCC/GND, or both modules wired
to the same bus.

No router, or a wrong WiFi password? After 25 seconds the board serves its own
network: join **`iris-face`** with password **`iriscalib`** and open
`http://192.168.4.1`. The eyes animate while it is still trying to connect, so
a frozen face always means a real fault rather than a slow boot.

## One brain, many bodies (the recommended 3-board setup)

```
                 your PC (IRIS = the only brain: voice, AI, decisions)
                          │  WiFi / HTTP
      ┌───────────────────┼───────────────────────┐
      ▼                   ▼                       ▼
 ESP32-S3            ESP32 "robot"           ESP32 "relays"
 face + sensors      BTS7960 x2 motors       lights/fans/sockets
 (2 OLED eyes,       (esp32-iris-node-       (esp32-iris-node, or your
  PIR/gas/light/      bts7960)                existing sketch + command map)
  ultrasonic)
```

If a board currently runs its **own** voice/AI code (mic + STT on the ESP):
remove it. Two listening brains fight over commands and the ESP's speech
recognition is far weaker than IRIS's. Keep the boards as simple HTTP bodies —
IRIS hears, thinks, and calls them.

## Many boards

Register as many as you want — each is just a name + IP:

```
add device kitchen light at 192.168.1.73 as relay
add device bedroom fan at 192.168.1.75 as relay
add device water pump at 192.168.1.76 as relay
add device robot at 192.168.1.74 as motor
```

`list my devices` shows them all; `check devices` pings every one.

## Troubleshooting

- **Upload fails: "Failed to connect to ESP32: No serial data received"** —
  the build and COM port are fine; the chip just didn't enter flash mode.
  Click Upload again and, while the terminal prints `Connecting....`, press
  and **hold the BOOT button** on the board until `Writing at 0x...` lines
  appear. Stronger version: hold BOOT, tap EN/RST once, keep holding BOOT.
  Still stuck? Disconnect driver/relay wiring for the first flash (a powered
  BTS7960/relay can back-feed pins and block boot mode), use a direct USB
  port and a known-good data cable, or add `upload_speed = 115200` under the
  env in `platformio.ini`.

- **"Could not reach the device"** — board and PC must be on the *same* WiFi
  network (not guest WiFi); check the IP in Serial Monitor; ping it from the PC.
- **Relay clicks inverted** — flip `RELAY_ACTIVE_LOW` in the sketch.
- **Robot turns the wrong way** — swap the IN1/IN2 (or IN3/IN4) wires or pins.
- **IP changes after reboot** — set a DHCP reservation in your router, or
  register the device with its `.local` name instead of the IP.
- **`.local` name not found on Windows** — install Apple Bonjour or just use the IP.
