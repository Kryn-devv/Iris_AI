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

### 1. Prepare Arduino IDE (once)
1. Install [Arduino IDE](https://www.arduino.cc/en/software).
2. *File → Preferences → Additional boards manager URLs*, add:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
3. *Tools → Board → Boards Manager* → install **esp32 by Espressif Systems**.

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

## Path B — keep your existing firmware

Your current boards (relay + web page on their own IP) keep working as-is.
Register the board, then map its existing URLs as named commands by editing
the registry file `devices.json` (Windows: `%APPDATA%\IrisAI`, Linux:
`~/.local/share/IrisAI`, macOS: `~/Library/Application Support/IrisAI`):

```json
{
  "devices": [
    {
      "name": "bedroom light",
      "base_url": "http://192.168.1.80",
      "kind": "relay",
      "commands": {
        "on":  "/led/on",
        "off": "/led/off",
        "toggle": "/led/toggle"
      }
    }
  ]
}
```

Whatever paths your firmware already answers (`/on`, `/relay1on`,
`/gpio?pin=5&val=1`, …) — put them in `commands` and "turn on the bedroom
light" will call them. For anything unusual:

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
