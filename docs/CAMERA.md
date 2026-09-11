# The robot's eye — ESP32-CAM

IRIS recognises your face and names what you hold up, through an **ESP32-CAM**
mounted on the robot's head. The camera is its own board; the thinking happens
on the laptop.

## Wiring — two wires

| ESP32-CAM pin | Goes to |
|---|---|
| **5V** | the robot's 5 V rail (a power bank or the buck set to 5 V — **not** the S3's 3.3 V pin: the camera pulls ~180 mA and spikes past 300 mA when the radio transmits) |
| **GND** | the common ground |

No data pins. The camera joins the same WiFi / phone hotspot (2.4 GHz) as the
S3 board and the laptop, and IRIS talks to it over HTTP. A DVP camera needs 15
GPIOs and the S3 has about six free, which is why it is not wired to the S3.

## Flash it

The firmware is in the [`cammodule`](https://github.com/Kryn-devv/cammodule)
repository, `esp32-cam/robot_eye/`. Put your hotspot name and password in
`config.h`, flash through the ESP32-CAM-MB programmer board (upload speed
115200), and read the address off the serial monitor. Its own page at that
address shows a live view and a `/motion` readout.

## Register it

```
add device eye at 192.168.43.42 as camera
```

Use the IP the serial monitor printed; `.local` names rarely resolve on a
hotspot. If you set `ACCESS_TOKEN` in the firmware, put `token=<value>` in the
device's notes.

## Teach it your face

Stand in front of the camera in decent light and say:

```
remember my face as Prakash
```

The first person introduced becomes the owner, which is what makes **"who am
I"** answer *"Yes, that's you."* Say it two or three more times in different
light and recognition gets steadier. Faces are compared **on the laptop**, never
sent anywhere, and live in `<data dir>/faces.json`.

Face recognition needs one of these installed (IRIS boots and works without —
it just says what to install):

```bash
pip install face-recognition          # dlib, 128-d. Needs a C++ toolchain.
pip install insightface onnxruntime   # ArcFace, 512-d. Downloads a model once.
pip install opencv-python-headless    # finds faces, cannot name them
```

Embeddings are not portable between backends: pick one before enrolling, and if
you switch later say "forget every face you know" and re-introduce yourself.

## Object identification — one setting

"What is this?" goes to a vision model, so it needs a model that accepts
images. In `.env`:

```ini
VISION_MODEL=meta-llama/llama-4-scout:free   # with OPENROUTER_API_KEY
# VISION_MODEL=gemini-flash-latest           # with GEMINI_API_KEY
```

Nothing else changes — the gateway already carries images. If a slow free model
times out, raise `PER_TOOL_TIMEOUT_SECONDS`.

## What to say

| Say | Tool | Needs |
|---|---|---|
| who am I · do you recognise me · who is that · main kaun hoon | `camera_who` | a face recogniser |
| what is this · what am I holding · ye kya hai | `camera_look` (object) | `VISION_MODEL` |
| what do you see · describe the room · kya dikh raha hai | `camera_look` (scene) | `VISION_MODEL` |
| read this · what does this say · ye kya likha hai | `camera_look` (text) | `VISION_MODEL` |
| is this ripe · does this look fresh | `camera_look` (ripeness) | `VISION_MODEL` |
| how many things can you see | `camera_look` (count) | `VISION_MODEL` |
| remember my face as X · learn my face as X | `camera_remember_face` | a face recogniser |
| forget my face · forget X's face · forget every face you know | `camera_forget_face` | — |
| who do you know · kisko pehchante ho | `camera_known_faces` | — |
| can you see anyone · is anyone in front of you · has anything moved · koi samne hai kya | `camera_presence` | — (the camera's own motion detector) |

When the camera finds a person, the S3 board's **OLED eyes turn toward them**
if a face device is registered — the camera hands over the position in the
same −100…100 coordinates the eyes' `/look` takes.

Left alone on purpose: "is there any motion" still asks the S3's PIR (it sees
the whole room; the camera only its own view), and "look at me" still turns the
OLED eyes.

## The camera's API

| Endpoint | Meaning |
|---|---|
| `GET /capture?size=xga&warmup=2&flash=1` | one JPEG; sizes qqvga…uxga |
| `GET /motion` | the camera's own movement detector: `moved`, `recent`, `look:{x,y}`, `box` |
| `GET /status` | firmware, uptime, `reset_reason` (`brownout` = the supply, not the code) |
| `GET /settings?…` | flip, mirror, brightness, quality — live, no reflash |
| `GET /stream` | MJPEG, port 81 |
