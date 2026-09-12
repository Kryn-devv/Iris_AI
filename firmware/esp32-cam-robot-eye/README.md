# The robot's eye — ESP32-CAM firmware

This folder is the firmware for the **AI-Thinker ESP32-CAM** on the robot's
head. It serves frames to IRIS over WiFi and watches for movement on its own.
Face recognition and object identification happen on the laptop
(`iris/app/vision/`) — this board only has to deliver a clean picture fast.

Files: `esp32-cam-robot-eye.ino` (the sketch), `motion.h` (the on-board
movement detector), `camera_pins.h` (pin maps for the common camera boards),
`config.example.h` (copy to `config.h` and fill in).

## Flash it (Arduino IDE, with the ESP32-CAM-MB board)

1. Push the camera onto the MB board. It only fits one way: lens facing away
   from the USB socket.
2. USB from the MB to the PC. Windows: if no COM port appears, install the
   **CH340** driver (`CH341SER`). Linux and macOS have it built in.
3. Arduino IDE → Boards Manager → install **esp32** by Espressif Systems
   (2.0.5 or newer).
4. **Tools → Board →** "AI Thinker ESP32-CAM" · **Partition Scheme →** "Huge
   APP" · **PSRAM →** "Enabled" · **Upload Speed →** `115200`. Faster speeds
   fail on the MB board's CH340 with *"timed out waiting for packet header"* —
   that is the speed, not a broken board.
5. Copy `config.example.h` to `config.h` and put your hotspot's name in
   `WIFI_SSID` and its password in `WIFI_PASSWORD`. `config.h` is ignored by
   git, so the password stays on your machine.
6. Open `esp32-cam-robot-eye.ino` and click Upload. If it says
   `Connecting........_____....` and fails: hold **IO0**, tap **RST**, release
   **IO0**, and click Upload again while it says "Connecting".
7. Press **RST** and open Serial Monitor at **115200**. It prints the camera's
   IP address and the exact line to say to IRIS.

## Power it

Leave the camera on the MB board and run one USB cable from a **power bank**
into the MB's USB socket. That is the only wire the camera needs: it talks to
IRIS over the hotspot. Use the shortest, thickest cable you have — the camera
pulls about 180 mA and spikes past 300 mA when the radio transmits, and a thin
cable causes brownout resets (`/status` shows `reset_reason: brownout`; the
camera page says *camera init failed*). Some power banks switch off below
~100 mA; the camera normally keeps them awake, and sharing the bank's second
port with the robot's ESP32 helps if yours sleeps.

## Connect it to IRIS

```
add device eye at 192.168.43.42 as camera        (use the IP it printed)
remember my face as Prakash
```

Everything else — face backends, `VISION_MODEL`, what to say, the watcher —
is in [`docs/CAMERA.md`](../../docs/CAMERA.md).

## Its own web page

`http://<ip>/` — live preview, test buttons, and these endpoints:

| Endpoint | Meaning |
|---|---|
| `GET /capture?size=xga&warmup=2&flash=1` | one JPEG; sizes qqvga…uxga |
| `GET /motion` | the movement detector: `moved`, `recent`, `look:{x,y}`, `box` |
| `GET /status` | firmware, WiFi, heap, uptime, `reset_reason` |
| `GET /settings?vflip=1&hmirror=1&…` | flip, mirror, brightness, quality — live, no reflash |
| `GET /flash?on=1&ms=200` | white LED, optionally a timed pulse |
| `GET :81/stream` | MJPEG live stream for a browser |

Different camera board? Set `CAMERA_MODEL_*` in `config.h`; `camera_pins.h`
carries the pin maps.
