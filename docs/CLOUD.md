# Running IRIS in the cloud, with your hardware at home

This is the whole mechanism, end to end: what runs where, how a board behind
your home router reaches a server on the internet, and how talking to the robot
turns into the robot answering you.

---

## 1. What runs where (read this first)

IRIS is a **Python application**. It needs an operating system and a few
hundred MB of RAM. An ESP32-S3 has 512 KB *total*, so the brain cannot live on
the board — and does not need to.

```
┌──────────────────────────────────────────────────────────────┐
│  THE BRAIN — IRIS (Python)                                   │
│  your PC, a Raspberry Pi, or a VPS                           │
│                                                              │
│  speech-to-text → agent kernel → tools → text-to-speech      │
│  the LLM gateway, memory, the scheduler, the web UI           │
└──────────────────────────────────────────────────────────────┘
                    ▲                    │
       sensor readings, speech           commands, speech audio
                    │                    ▼
   ┌────────────────────────┬────────────────────────┬─────────────────────┐
   │  ESP32-S3  "face"      │  ESP32  "robot"        │  ESP32  "relays"    │
   │  2 OLED eyes           │  2× BTS7960, 4 motors  │  lights, fans,      │
   │  PIR · gas · light     │                        │  sockets            │
   │  flame · ultrasonic    │                        │                     │
   │  I2S mic + speaker     │                        │                     │
   └────────────────────────┴────────────────────────┴─────────────────────┘
```

Every board is a **body**: it has no AI on it, it just does what it is told and
reports what it senses. One brain, many bodies. Add a fourth board and nothing
about the brain changes.

A **laptop, tablet or phone is not a body** — it is a window. The dashboard is
a web page served by the brain, so any device with a browser can watch and
control everything with nothing installed on it. See **§3.8** if that is how
you intend to use it.

---

## 2. The two ways a board reaches the brain

This is the part that decides everything else, so it is worth being precise.

### Mode A — brain at home (simplest)

IRIS runs on your PC or a Raspberry Pi on the same WiFi. **IRIS calls the
board**: it does an HTTP request to `192.168.1.70`. Nothing is exposed to the
internet, latency is a few milliseconds, and it works today with no extra
setup.

```
IRIS (192.168.1.5)  ──HTTP GET /face?emotion=happy──▶  board (192.168.1.70)
```

### Mode B — brain in the cloud (your VPS)

Now IRIS is on a VPS with a public address, and the board is at home behind
your router. **IRIS cannot call the board.** Your router does NAT: it has one
public address shared by every device in the house, and no rule that says which
device an unexpected incoming connection belongs to. There is no address that
reaches your ESP32 from the internet.

The usual suggestion is port-forwarding. Don't: it would put a
microcontroller's unauthenticated web server on the public internet, where it
will be found within days.

**So the direction flips. The board calls out to IRIS and keeps the line open.**

```
board (at home)  ──opens a WebSocket──▶  IRIS (your VPS)
                 ◀── commands come back down the same connection
                 ── sensor readings go up it continuously
```

An outbound connection is exactly what NAT is designed to allow, so this needs:

- no port-forwarding
- no static home IP
- no dynamic DNS
- nothing at all exposed on your home network

The board reconnects by itself when your internet drops or the VPS restarts,
with a backoff. While the link is down the board stays fully usable on your
LAN — the eyes keep animating and its own web page keeps working.

**Both modes can be on at once.** Set `CLOUD_HOST` and the board dials out;
leave it empty and it waits to be called.

---

## 3. Putting IRIS on your VPS (Pterodactyl)

Yes — a Python egg on Pterodactyl is exactly the right shape for this. IRIS is
one process that listens on one port, which is what the panel expects.

### 3.1 Create the server

1. In Pterodactyl, create a server using a **Python** egg (any generic
   "Python App" / "Python Generic" egg works).
2. Give it a **port allocation** — note the port number, e.g. `25580`.
3. Give it as much RAM as you can spare. **1 GB is the realistic minimum**;
   2 GB is comfortable. Speech-to-text is the memory-hungry part (see 3.5).

### 3.2 Fill in the egg's variables — the panel clones the code for you

Use a **generic Python egg** (any "Python App" / "Python Generic"). Docker
image: **Python 3.11**. Do not edit the startup command; the repository has a
top-level `app.py` precisely so the egg's defaults work unchanged.

| Egg variable | Value |
|---|---|
| **Git Repo Address** | `https://github.com/Kryn-devv/Iris_AI.git` |
| **Git Branch** | `main` |
| **App py file** | `app.py` *(the default — leave it)* |
| **Requirements file** | `requirements.txt` *(the default — leave it)* |
| **Auto Update** | `1` — pulls the latest code on every start |
| **Additional Python packages** | leave empty; see §3.5 if you want voice |
| **Git Username / Access Token** | leave empty (the repo is public) |
| **User Uploaded Files** | `0` |

Press install. The egg clones the repo into `/home/container`, installs
`requirements.txt`, and runs `python /home/container/app.py`.

`app.py` is a thin shim over `python -m iris --headless`. It does the two
things a container knows and a laptop does not: it turns off the tray icon and
the open-a-browser step before anything reaches for a desktop that isn't there,
and it binds `0.0.0.0` on **the port the panel assigned** (`SERVER_PORT`), so a
changed allocation does not need `.env` edited to match. An explicit `PORT` in
`.env` still wins.

### 3.3 Why not edit the startup command

You can, but there is nothing to gain and one thing to lose: a hand-edited
startup command is invisible to anyone reading the repository, so the next
person to debug the deployment is reading a command you wrote and they cannot
see. `app.py` keeps that decision in version control.

### 3.4 The `.env` file

Create `/home/container/.env`:

```ini
# You do NOT need HOST, PORT, ALLOW_LAN_ACCESS, OPEN_BROWSER_ON_START or
# TRAY_ENABLED here — app.py sets all five for you, and reads the port from
# the panel's own allocation. Set PORT only to override that deliberately.
API_TOKEN=pick-a-long-random-string

# let your boards dial in — REQUIRED for Mode B
NODE_LINK_TOKEN=another-long-random-string

# at least one free AI provider
GROQ_API_KEY=gsk_...
```

Two different tokens, and they do different jobs:

| Token | Who uses it | What it protects |
|---|---|---|
| `API_TOKEN` | you, from a browser or your phone | the web UI and the normal API |
| `NODE_LINK_TOKEN` | your ESP32 boards | the socket that switches relays and drives motors |

**`NODE_LINK_TOKEN` is required, not optional.** With it unset, IRIS refuses
every node connection rather than accepting anonymous ones — because that
channel can switch mains relays. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3.5 Speech-to-text on a small server

This is the one place a cheap VPS struggles, so choose deliberately:

| Option | RAM | Quality | Set in `.env` |
|---|---|---|---|
| `faster_whisper` + `tiny` model | ~500 MB | decent | `STT_ENGINE=faster_whisper` / `STT_MODEL=tiny.en` |
| `faster_whisper` + `base` model | ~1 GB | good | `STT_MODEL=base.en` |
| `google_free` | almost none | good | `STT_ENGINE=google_free` |

`google_free` sends the audio to Google's free endpoint instead of running a
model locally — no RAM cost, but the audio leaves your server. Pick whichever
trade you prefer; both work.

For **text-to-speech**, install `piper`. It is small, fast on CPU, and produces
WAV, which is what a speaker on an ESP32 can play without a decoder. Without a
WAV-producing engine the board's speaker cannot be fed at all.

### 3.6 Get TLS in front of it

Put the VPS behind a reverse proxy with a real certificate (Caddy is two lines,
Nginx + certbot works too), so the boards can connect with `wss://` and
`https://`. Then in the firmware: `CLOUD_PORT = 443`, `CLOUD_TLS = true`.

Without TLS the node token travels the internet in clear text. The firmware
prints a warning at boot if you do that, and it means what it says.

### 3.7 What changes when the brain moves to the cloud

**Almost nothing does.** Of 79 tools, about 24 need a real screen and keyboard,
and those are the only ones affected. Everything else runs identically — often
better, because a VPS is awake when your laptop is not.

**These keep working, unchanged:**

| | |
|---|---|
| **Presentations, documents, spreadsheets, code** | `create_presentation` uses python-pptx, `write_document` python-docx, `create_spreadsheet` openpyxl. All pure Python, no screen involved. The file appears as a **download link in the conversation** — click it and it lands on your laptop |
| Every device command | robot, relays, servo, face, sensors |
| The voice loop | speech-to-text, the LLM, text-to-speech |
| Conversation, memory, reminders, routines, the scheduler | |
| Web search, page fetch, Wikipedia, weather, news | |
| Reading and writing files, searching them | inside the sandbox, on the VPS |
| Telegram | |
| **Opening a website** | see below — this one used to break and no longer does |

**"Open youtube" opens a tab on YOUR machine.** There is no desktop on the
VPS, so instead of failing, IRIS pushes the URL down the WebSocket and the
dashboard opens it in the browser you are actually looking at. Browsers only
allow new tabs from a click, so the dashboard also puts the link in the
conversation as a button — that path always works.

**These genuinely cannot work from the cloud**, because they act on a physical
screen that isn't there:

- `open_app` / `close_app` / `list_apps` — launching programs
- `take_screenshot`, `list_windows`, `focus_window`, `minimize/maximize_window`
- `type_text`, `press_keys`, `mouse_click`, `mouse_move`, `scroll`
- `clipboard_read` / `clipboard_write`
- `volume`, `media_control`
- `lock_screen`, `sleep_pc`, `shutdown_pc`, `restart_pc`
- `notify` (a desktop notification popup)
- `open_path` / `find_and_open` — opening a file in its native app

They will report a clear failure rather than pretending to have worked. If you
want those **and** a cloud brain, run IRIS at home and reach it from outside
with a tunnel instead (`TUNNEL_PROVIDER=cloudflared`) — same UI, same URL from
anywhere, but the desktop it drives is your own PC.

### 3.8 Using a laptop as a display only — nothing installed on it

This is the normal way to use a cloud brain, and it needs no special mode.
**IRIS's dashboard is a web page.** The VPS serves it; the laptop opens it.
Nothing is installed on the laptop — no Python, no repository, no service, not
a single file. If the laptop can open a web page, it can show the dashboard.

**The whole procedure:**

1. On the VPS, in `.env`, have the three settings from §3.4 —
   `HOST=0.0.0.0`, `ALLOW_LAN_ACCESS=true`, and an `API_TOKEN` you choose.
   Put TLS in front of it (§3.6) so the address is `https://`.
2. On the laptop, open a browser and go to, once:

   ```
   https://iris.yourdomain.com/?token=YOUR_API_TOKEN
   ```

3. The page stores the token in the browser and drops it from the address bar.
   From then on the bare `https://iris.yourdomain.com` is enough on that
   laptop. Bookmark it.

That is it. The dashboard, the hologram, the conversation, the device panels,
the sensor readings — all of it is drawn from data the VPS pushes down a
WebSocket. Close the tab and IRIS keeps running on the VPS; reopen it and the
current state is there. Open it on a phone and a laptop at the same time and
both show the same live state, because neither of them holds any of it.

**Where each piece actually executes:**

| Piece | Runs on |
|---|---|
| The agent, tools, memory, LLM calls, scheduler | the VPS |
| Speech-to-text, text-to-speech | the VPS |
| Every device command (relays, servo, robot, sensors) | the VPS → the boards |
| Drawing the page: layout, the hologram canvas | the laptop's browser, like any website |

**The one thing worth deciding.** The dashboard can use the laptop's
microphone and speaker for talking to IRIS — that is the browser's own audio
permission, the same one a video call uses, not software you installed. If even
that is more than you want on the laptop, don't grant the permission: put the
I2S microphone and speaker on the S3 board (§6) and talk to the robot instead.
The laptop then shows the conversation happening without taking any part in it.

**You do not lose the features, only the screen-poking ones.** Presentations,
documents, spreadsheets and code are built by pure-Python libraries on the VPS
and arrive as a **download link in the conversation** — click it and the .pptx
is on your laptop. Roughly 24 of 79 tools need a physical screen (launching
programs, screenshots, mouse and keyboard, volume, shutdown); §3.7 lists them
precisely. Everything else is unaffected.

**A guarantee you get for free.** Those screen-poking tools act on the machine
IRIS runs on, which here is the VPS. "Open notepad" cannot reach the laptop
even if you ask for it, because the laptop is not where IRIS is — a browser tab
has no way to launch a program on the computer showing it. If that is the rule
you have to satisfy, this setup satisfies it by construction.

> Sharing the dashboard on a LAN instead of the internet works identically:
> the same three settings, and the address is the machine's LAN IP
> (`http://192.168.1.20:8756/?token=...`). The token is what makes that safe.

---

## 4. Flashing the S3 for cloud mode

In `firmware/esp32-s3-iris-sensors/esp32-s3-iris-sensors.ino`:

```cpp
const char* WIFI_SSID   = "your wifi";
const char* WIFI_PASS   = "your wifi password";
const char* DEVICE_NAME = "face";

const char* CLOUD_HOST  = "iris.example.com";   // your VPS
const uint16_t CLOUD_PORT = 443;
const bool CLOUD_TLS    = true;
const char* CLOUD_TOKEN = "another-long-random-string";  // = NODE_LINK_TOKEN
```

That is the whole configuration. Upload it, and watch the serial monitor:

```
[cloud] linked to IRIS
```

**You do not have to register the device.** IRIS registers a node the moment it
dials in — there is no address to type, because a linked node does not have one.
Check from IRIS:

```
list my devices
```

or open `https://your-vps/api/v1/nodes` in a browser (with your `API_TOKEN`).

---

## 5. Wiring the sensors (including the flame sensor)

| Sensor | Pin | Notes |
|---|---|---|
| PIR HC-SR501 OUT | GPIO 4 | 3.3V output — connect directly |
| MQ-2 gas AO | GPIO 5 | ⚠ through a 1k/2k divider (AO reaches ~4V) |
| LDR divider midpoint | GPIO 6 | LDR + 10k from 3.3V |
| HC-SR04 TRIG | GPIO 7 | direct |
| HC-SR04 ECHO | GPIO 8 | ⚠ through a 1k/2k divider (ECHO is 5V) |
| **Flame module DO** | **GPIO 13** | 3.3V output — direct. Use **DO**, not AO |

**The S3's pins are not 5V tolerant.** Skipping either divider can kill the
input permanently. Divider recipe: `signal --[1k]--+--[2k]-- GND`, and the ESP32
pin goes to the `+` tap.

**Analog sensors must be on GPIO 1–10.** GPIO 11–20 are ADC2, and ADC2 stops
working the moment WiFi comes up — the reading silently returns garbage rather
than failing. The firmware warns at boot if you have put one there.

**Flame modules are usually active-LOW**: they pull DO *low* when they see
fire, which is the opposite of what "HIGH means yes" intuition suggests. The
default `FLAME_ACTIVE_LOW = true` matches the common blue IR module. If your
board reports fire constantly, or never, flip that one line.

### What gets sent, and when

| When | What |
|---|---|
| every ~5 seconds | all readings, so "any motion?" is answered instantly |
| the moment it changes | motion appearing or clearing, distance moving > 8 cm |
| **immediately** | **flame or gas** — IRIS says it out loud without being asked |

That last row is why the flame sensor is worth having. You do not ask "is there
a fire"; the board tells IRIS, and IRIS speaks:

> "Fire detected! There is a flame near face."

and the robot's eyes go wide. Repeats are suppressed for 90 seconds, because a
sensor sitting right on its threshold flickers and a voice repeating a fire
warning every second is worse than useless.

You can still ask directly, any time:

```
is there a fire        ·  aag lagi hai kya
is there any motion    ·  koi hai kya
what's the gas level   ·  gas level kya hai
how far is the object  ·  kitna door hai
check the sensors
```

For a linked node these are answered from the readings already pushed up — no
round trip to the other side of the internet.

---

## 6. Wiring the microphone and speaker

You need **I2S** parts, not analog ones. The KY-038 / analog "sound sensor"
modules can only tell you *that* there is noise, not *what* was said.

### Microphone — INMP441 or ICS-43434 (~£3)

| Mic pin | To |
|---|---|
| VDD | 3.3V |
| GND | GND |
| SCK | GPIO 14 |
| WS | GPIO 15 |
| SD | GPIO 16 |
| L/R | **GND** (selects the left channel, which is what the firmware reads) |

### Speaker — MAX98357A I2S amplifier (~£4) + any 4Ω/8Ω speaker

| Amp pin | To |
|---|---|
| VIN | **5V** |
| GND | GND |
| BCLK | GPIO 17 |
| LRC | GPIO 18 |
| DIN | GPIO 21 |
| SD | leave floating (that means unmuted) |
| + / − | your speaker |

> **Power matters here.** A 3W amplifier driving a real speaker is by far the
> biggest current draw on the board. Running it from a weak USB supply is the
> usual cause of "it reboots whenever it talks". Give the amp its own 5V feed
> from the same supply that powers your motors, with grounds common.

### How the voice loop actually works

```
1. the mic is read continuously into a small ring buffer  (nothing leaves the board)
2. the level rises  →  an HTTPS POST opens and starts uploading
                       ...including ~100 ms from BEFORE you started talking,
                       so the first syllable is not clipped
3. you stop talking (0.8 s of quiet)  →  the upload closes
4. IRIS transcribes → runs the agent → speaks the reply → sends back a WAV
5. the WAV streams straight to the speaker as it arrives
6. the eyes bounce for exactly the length of the reply
```

**Why the detection happens on the board.** Streaming the microphone to the
cloud all day would cost bandwidth, cost privacy, and cost money. So the board
decides locally whether anyone is speaking and only then opens a connection.
That is a level threshold, not speech recognition — it will trigger on a door
slam, and IRIS simply finds nothing to transcribe and answers nothing.

**Tuning it**, in the firmware:

| If | Change |
|---|---|
| it never triggers | lower `VOICE_START_LEVEL` (default 1400) |
| it triggers on room noise | raise `VOICE_START_LEVEL` |
| IRIS mishears you | raise `MIC_GAIN` (default 4) |
| audio sounds clipped/harsh | lower `MIC_GAIN` |
| it cuts you off mid-sentence | raise `VOICE_SILENCE_MS` (default 800) |
| it waits too long before answering | lower `VOICE_SILENCE_MS` |

Prefer a button? Set `PIN_PTT` to a GPIO with a button to ground, and it
records only while held.

---

## 7. Home appliances — where the relay board fits

Nothing changes for the relay board. It is the same "body" pattern, and it has
two paths depending on what is already on it.

### If you flash the IRIS relay firmware

`firmware/esp32-iris-node/` with `DEVICE_KIND = "relay"`. Then:

```
add device kitchen light at 192.168.1.73 as relay
turn on the kitchen light      ·  light chalu karo
switch off the fan             ·  fan band karo
```

### If it already runs your own code (recommended — you already wrote it)

**Don't reflash it.** IRIS does not care what code is on the board, only that
it answers an HTTP GET. Register the IP you already have and map your existing
URLs by voice:

```
add device hall light at 192.168.1.40 as relay
map hall light on command to /relay1on
map hall light off command to /relay1off
```

From then on "turn on the hall light" calls exactly `/relay1on`.

### All three boards dial out

Every firmware in this repo can reach a VPS-hosted IRIS, because every one of
them dials *out*:

| Board | Sketch | Set |
|---|---|---|
| sensors + eyes + voice | `esp32-s3-iris-sensors` | `CLOUD_HOST`, `CLOUD_TOKEN` |
| relays + servo | `esp32-iris-node` | `CLOUD_HOST`, `CLOUD_TOKEN` |
| robot base (2× BTS7960) | `esp32-iris-node-bts7960` | `CLOUD_HOST`, `CLOUD_TOKEN` |

`CLOUD_TOKEN` must equal `NODE_LINK_TOKEN` in IRIS's `.env`. Leave `CLOUD_HOST`
empty on any board you want to keep LAN-only — the two modes coexist, and the
board's own web page keeps working either way.

The robot node's **calibration endpoints work over the link too**, not just
`/motor`. That matters precisely because the one time you cannot walk over to
the robot and open its page is when it is somewhere else.

**A board running *your own* firmware is the exception.** A custom sketch has
no way to dial out, so with IRIS on a VPS you either flash one of the sketches
above, or keep IRIS at home and reach it from outside with a tunnel
(`TUNNEL_PROVIDER=cloudflared`).

---

## 8. The whole thing, in order, when you speak

```
you:  "IRIS, is there a fire in the kitchen?"

  1.  the mic on the S3 hears you rise above the threshold
  2.  the S3 opens an HTTPS POST to your VPS and uploads while you talk
  3.  IRIS transcribes it                              (speech-to-text)
  4.  the deterministic rule catalogue matches "is there a fire"
      → device_sensors(sensor="flame")                  (no LLM needed, instant)
  5.  the flame reading is already on the server — pushed up seconds ago —
      so there is no round trip back to the board
  6.  IRIS builds the sentence: "no flame, gas level 740 (normal)"
  7.  the face service reads that sentence, infers "neutral", and pushes it
      down the WebSocket with the reply's length
  8.  the eyes settle and begin the talking bounce
  9.  piper turns the sentence into a WAV
 10.  the WAV streams back down the still-open POST response
 11.  the S3 plays it through the MAX98357A as it arrives
 12.  the eyes stop bouncing exactly when the audio ends
```

Steps 4–6 are why the sensor telemetry is pushed rather than polled: the
answer to a sensor question is already sitting on the server when the question
arrives.

---

## 9. Security, plainly

| Thing | How it is protected |
|---|---|
| the node socket | `NODE_LINK_TOKEN`. Unset ⇒ every connection refused, not allowed |
| the node voice endpoint | the same token, checked before any audio is read |
| the web UI and API | `API_TOKEN`, enforced for every non-local client |
| device addresses | LAN-only. IRIS refuses to send a device command to a public address, so a bad reply cannot redirect a relay command to the internet |
| your home network | nothing is exposed. The board dials out; no port is forwarded |
| the token in transit | only as safe as TLS. Use port 443 and `CLOUD_TLS = true` |

Two things worth knowing rather than discovering:

- With `CLOUD_TLS_VERIFY = false` (the default, for convenience) the board does
  not check the server's certificate. That is fine on your own LAN; over the
  internet it means someone who can intercept your traffic could impersonate
  your server and collect the token. Set it to `true` and paste your CA in if
  that matters to you.
- Anything a node sends is treated as untrusted input on the server: frames are
  size-capped, readings are type-checked, and a chatty node is rate-limited so
  it cannot flood the event bus.

---

## 10. When something does not work

| Symptom | Cause |
|---|---|
| serial shows nothing after "Connecting to WiFi" | wrong SSID/password — after 25 s the board serves its own network `iris-face` / `iriscalib` at `192.168.4.1` |
| `[cloud] link lost` on repeat | wrong `CLOUD_TOKEN`, wrong port, or no TLS proxy in front of the VPS |
| IRIS logs "Node links idle" | `NODE_LINK_TOKEN` is not set in `.env` |
| device commands say "not connected right now" | the board is not dialled in — check its serial output |
| the mic never triggers | `VOICE_START_LEVEL` too high, or L/R not tied to GND |
| replies are silent | no WAV-capable TTS on the server — install `piper` |
| "Could not make out any speech" | no STT engine installed, or `MIC_GAIN` too low |
| the board reboots when it talks | the amplifier is browning out the supply |
| gas/light readings are nonsense | the sensor is on GPIO 11–20 (ADC2) — move it to 1–10 |
| the flame alarm is always on | your module is active-HIGH: set `FLAME_ACTIVE_LOW = false` |

See also: [ESP32.md](ESP32.md) for wiring each board, and the robot node's own
calibration page for motor directions.
