# Where the project honestly stands

Written in plain language, no jargon, so anyone picking this up — including
future you — knows exactly what works and what the one next step is for each
piece.

## Working

- **The assistant itself**, on the Mac. Start it with
  `.venv/bin/python -m iris` inside the repo folder, open
  `http://127.0.0.1:8756`. Real AI replies via Groq (the key lives in `.env`
  next to this file). Themes, the cosmic UI, presentations, documents — all of
  it.
- **The microphone**, on the Mac. This was never possible on the VPS: browsers
  only allow mic access on `https://` or localhost, full stop. Locally it just
  works — click the mic, allow the permission.
- **The face/sensor board (ESP32-S3)**: joins WiFi and registers.
  `add device face at face.local as face`, then `check the sensors`,
  `show happy eyes`.
- **The robot board**: joins WiFi (its sketch had SSID `"IO"` instead of
  `"ARS"` — each sketch carries its own WiFi lines) and serves its calibration
  page. Set `AP_ONLY = true` in its sketch and it instead serves its own
  network `iris-robot` / `iriscalib` at `http://192.168.4.1` — the strongest
  possible link for calibrating.

## Fixed, needs re-flashing to take effect

- **Motors — the firmware was hanging on the first drive command.** This was
  never wiring, and no amount of testing the hardware was going to find it.
  Two functions in the robot sketch read every command's arguments, and the
  HTTP half of both of them **called themselves** instead of calling the web
  server:

  ```cpp
  static bool argHas(const char* name) {
    return cloudArgs ? cloudArgs->has(name) : argHas(name);   // <- itself
  }
  ```

  So every drive command was an endless self-call. The compiler turns that
  into a loop without warning, so the board did not crash with an error —
  it just stopped, having never written a single duty cycle. `/status` takes
  no arguments, which is why the calibration page loaded perfectly and only
  *movement* was dead. That is indistinguishable from unpowered drivers, and
  it is where the hardware bisection went.

  Reflash `firmware/esp32-iris-node-bts7960/` and it drives.

- **Slowness.** Also not the motors. Arduino's `WebServer` serves one client
  at a time and holds a socket that has connected but said nothing for up to
  five seconds, accepting nothing else — and browsers open exactly such idle
  sockets, so having the calibration page open queued the next drive command
  behind it. The drive path now goes over UDP (port 8267, what IRIS uses) and
  a pushed WebSocket (port 81, what the page uses), neither of which touches
  that server. HTTP still answers everything, so nothing you have breaks.
  The page now shows the real measured round trip in milliseconds, so you can
  see it rather than take my word for it.

  On the IRIS side, every device command was building a fresh HTTP client,
  which costs **43 ms** of CPU before a packet leaves (26 ms reading proxy
  settings out of the environment, 16 ms building an SSL context it never
  used). Now 0.19 ms.

  Four more silent "answers 200 and does not move" paths went with it, the
  worst being that `/motor` treated a **missing or misspelled `dir` as a
  stop** — so one typo looked exactly like broken wiring.

## Retired

- **The relay node.** The 4-channel relay board, its firmware
  (`esp32-iris-node`), the `relay` device kind, the switch and servo
  commands and the 12 V wiring sheet are gone. The hardware is two boards
  now: the robot base and the S3 face/sensor board. A `devices.json` that
  still lists a relay loads it as a plain `generic` device, so nothing
  breaks on upgrade — it just no longer means anything.

## Still to do — by hand, not by code

- **Rotate the Groq key.** It was on screen during a shared session. Make a
  new one at console.groq.com, put it in `.env`, delete the old one there.
- **Change the two shared tokens.** `API_TOKEN` and `NODE_LINK_TOKEN` are
  both still the short word they were set to for testing. Generate real
  ones (`python -c "import secrets; print(secrets.token_urlsafe(32))"`),
  put the node one in both firmwares' `CLOUD_TOKEN`, and reflash.
- **Flashing fails with "port busy."** Something still holds the USB
  port — usually a Serial Monitor, sometimes one in a *second* VS Code window.
  `lsof /dev/cu.usbmodem*` names the holder; quitting VS Code entirely and
  reopening one window always clears it.

## Things learned the hard way (so they're not relearned)

- The VPS path can never grant microphone access over plain `http://`. Local
  is the right home for this project unless you put real HTTPS in front.
- Edited copies of the firmware folders outside the git clone don't update on
  `git pull`. Work inside `Iris_AI/firmware/...` only.
- Each firmware sketch has its own `WIFI_SSID`/`WIFI_PASS` — fixing one board
  does not fix the others.
- `[E] request handler not found` in a board's log was only ever the browser
  asking for a tab icon. Fixed; it was never a fault.
- **"The motors don't move" is a firmware claim, not a wiring claim, until the
  firmware has been shown to reach the pins.** A plain sketch driving the same
  motors on the same pins proved the hardware in five minutes and the real bug
  still took hours to find, because everyone kept looking at the robot. When a
  board answers HTTP 200 and nothing happens, the next question is what the
  code did with that request — not which wire is loose.
- Anything that can answer "OK" without doing the thing will eventually do
  exactly that. Every one of this session's bugs was that shape: a self-calling
  function that returned before acting, a missing `dir` that meant "stop", a
  saved trim of 0 that scaled a side to nothing. The firmware now refuses
  instead of defaulting, in each case.
