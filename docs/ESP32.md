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

### 3. Wiring (typical)
**Relay module:** ESP32 `5V/VIN → VCC`, `GND → GND`, `GPIO26 → IN1`,
`GPIO27 → IN2`, … Mains wiring goes through the relay's COM/NO terminals —
be careful with mains voltage; if unsure, switch a 12V strip or use a smart-plug-style low-voltage load.

**L298N motor driver:** `GPIO25 → ENA`, `GPIO13 → IN1`, `GPIO12 → IN2`,
`GPIO14 → ENB`, `GPIO21 → IN3`, `GPIO22 → IN4`, common GND between ESP32 and
driver, motor battery to the driver's 12V input.

> **Using BTS7960 instead of L298N?** (Common for a 4-wheel-drive robot with
> two driver boards — one per side.) Don't use this sketch's motor section.
> Flash **`firmware/esp32-iris-node-bts7960/esp32-iris-node-bts7960.ino`**
> instead — same `/motor` API, wired for two BTS7960 boards. See its own
> wiring table below.

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
```

> Tip: give your router a DHCP reservation for each board (or use the
> `http://<name>.local` mDNS address) so the IP never changes.

---

## Robot with BTS7960 drivers (2 boards, 4-wheel-drive)

If your robot uses **two BTS7960 modules** — one driving both left motors,
one driving both right motors (the standard skid-steer 4WD wiring) — use
`firmware/esp32-iris-node-bts7960/esp32-iris-node-bts7960.ino`, **not** the
plain `esp32-iris-node.ino` (that one is wired for L298N and won't drive a
BTS7960 correctly).

### Wiring — per BTS7960 board

| BTS7960 pin | Connect to |
|---|---|
| RPWM | an ESP32 GPIO (forward speed) |
| LPWM | an ESP32 GPIO (reverse speed) |
| R_EN **and** L_EN | tied together, to one more ESP32 GPIO |
| B+ / B− | your motor battery — **never** the ESP32's own 5V pin |
| GND | shared with the ESP32 **and** the battery **and** the other BTS7960 |

Default pins in the sketch (edit if your wiring differs):

| Side | RPWM | LPWM | EN |
|---|---|---|---|
| Left  | GPIO 25 | GPIO 26 | GPIO 27 |
| Right | GPIO 32 | GPIO 33 | GPIO 14 |

If a wheel spins the wrong way, swap that side's RPWM/LPWM wires — don't
edit the code for it.

### Flash it
1. Open the `.ino`, fill in `WIFI_SSID` / `WIFI_PASS`
2. Board: **ESP32 Dev Module** (or whatever your board's label says — S3
   boards need "ESP32S3 Dev Module" instead)
3. Upload → open **Serial Monitor at 115200** → it prints an IP address
4. Tell IRIS: `add device robot at <that IP> as motor`
5. Test: `robot forward`, `robot stop`, `move the robot left`

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
| `GET /motor` | `/motor?dir=forward&speed=200&ms=1500` | drive; auto-stops after `ms` |

Timed moves auto-stop even if WiFi drops mid-command (the deadline runs on the
board), and the board reconnects to WiFi by itself.

## Sensor node (ESP32-S3 with PIR / gas / light / ultrasonic)

Flash `firmware/esp32-s3-iris-sensors/esp32-s3-iris-sensors.ino` on the S3
(board: **ESP32S3 Dev Module**). Default pins — change them in CONFIG:

| Sensor | Pin | Note |
|---|---|---|
| PIR HC-SR501 OUT | GPIO 4 | 3.3V output, connect directly |
| MQ-2 gas AO | GPIO 5 | ⚠ through a 1k/2k voltage divider (AO can reach ~4V) |
| LDR divider midpoint | GPIO 6 | LDR + 10k resistor from 3.3V |
| HC-SR04 TRIG | GPIO 7 | direct |
| HC-SR04 ECHO | GPIO 8 | ⚠ through a 1k/2k divider (ECHO is 5V) |

**The S3's pins are NOT 5V tolerant** — skipping the two dividers can kill
inputs. Power PIR/MQ-2/HC-SR04 from the 5V pin, the LDR from 3.3V. Set any
unused sensor's pin to `-1`.

Register and ask:

```
add device room sensor at 192.168.1.70 as sensor
is there any motion       ·  koi hai kya
what's the gas level      ·  gas level kya hai
how far is the object     ·  kitna door hai
check the sensors
```

## One brain, many bodies (the recommended 3-board setup)

```
                 your PC (IRIS = the only brain: voice, AI, decisions)
                          │  WiFi / HTTP
      ┌───────────────────┼───────────────────────┐
      ▼                   ▼                       ▼
 ESP32-S3            ESP32 "robot"           ESP32 "relays"
 sensor node         L298N motors            lights/fans/sockets
 (this firmware)     (esp32-iris-node,       (esp32-iris-node, or your
                      MOTORS_ENABLED=true)    existing sketch + command map)
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

- **"Could not reach the device"** — board and PC must be on the *same* WiFi
  network (not guest WiFi); check the IP in Serial Monitor; ping it from the PC.
- **Relay clicks inverted** — flip `RELAY_ACTIVE_LOW` in the sketch.
- **Robot turns the wrong way** — swap the IN1/IN2 (or IN3/IN4) wires or pins.
- **IP changes after reboot** — set a DHCP reservation in your router, or
  register the device with its `.local` name instead of the IP.
- **`.local` name not found on Windows** — install Apple Bonjour or just use the IP.
