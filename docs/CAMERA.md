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

## It watches on its own

You do not have to ask. From the moment IRIS starts, the eye is *attentive*:

- **You walk in** → *"Good evening, Prakash. Good to see you."* Once, and then
  not again for five minutes however long you stand there. Anyone else it has
  been introduced to gets *"Good evening, Aditi."*
- **A stranger walks in** → *"Someone I don't recognise is here."*
- **You set something down in front of it** and it stops moving → the vision
  model names it: *"I see a red apple."* Once per thing; it stays quiet for an
  empty room or a hand passing through.
- The **OLED eyes turn toward** whoever it found, and light up happy for a
  friend, wary for a stranger.

How it stays cheap: once a second it asks the camera's own motion detector
(`/motion`, a few bytes of JSON). Only when something moved does it fetch a
frame, and only when there is no face in that frame does it spend a
vision-model call. Nothing happens for an empty room. If the camera is
unplugged it stops trying for a minute and logs one line.

| Say | Effect |
|---|---|
| stop watching · pause watching · dekhna band karo | pauses it until asked again |
| start watching · keep an eye out · dekhte raho | resumes, and greets you again straight away |
| are you watching · what have you seen so far | status: camera, counts, last person seen |

Settings in `.env` (all optional): `CAMERA_WATCH_ENABLED`, `CAMERA_WATCH_INTERVAL_S`,
`CAMERA_GREET_COOLDOWN_S`, `CAMERA_ANNOUNCE_STRANGERS`, `CAMERA_WATCH_OBJECTS`,
`CAMERA_OBJECT_COOLDOWN_S`. Object naming needs `VISION_MODEL`; greetings need a
face recogniser (above). With neither installed it still runs and does nothing,
and `are you watching` tells you exactly what it can and cannot do.

## Powering the camera through its motherboard

The ESP32-CAM-MB programmer board is a fine permanent home for the camera, not
just a flashing tool. Leave the camera plugged into it and run one USB cable
from a **power bank** into the MB's USB port: the MB feeds 5 V to the camera's
5V pin and nothing else on it interferes with WiFi. The camera then needs **no
wire at all** to the rest of the robot — it talks over the hotspot.

Two things to get right:

1. **A short, thick cable and a decent power bank.** The camera pulls about
   180 mA and spikes past 300 mA when the radio transmits. A thin 1 m cable drops
   enough voltage to cause *brownout* resets (`/status` shows
   `reset_reason: brownout`, and the camera page says *camera init failed*).
   Use the shortest cable you have.
2. **A power bank that stays on at low current.** Some banks switch off below
   ~100 mA. The camera's draw is normally enough to keep them awake; if yours
   sleeps, a bank with an *always-on / low-current mode* fixes it, or share the
   bank's second port with the robot's ESP32 so the total draw is higher.

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
