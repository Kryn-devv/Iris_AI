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

## Not working yet — and the single next step for each

- **Motors.** Untested at module level. On the robot's page, wheels off the
  ground, press `A fwd` then `B fwd`. The result is the whole diagnosis:
  neither moves → the 12V battery isn't on the drivers' B+/B−; one side dead →
  that module's VCC has no 5V (the logic side consumes 5V, it doesn't make it)
  or its R_EN+L_EN aren't tied to the EN pin; both move → it's only
  calibration: hold forward, flip swap/invert until forward is forward, SAVE.
- **Relays / home automation.** Most likely `RELAY_ACTIVE_LOW` is wrong for
  the module (flip it to `false` and reflash), or the module's VCC has no 5V.
  Test on the board's own page first — the buttons are labelled with their
  GPIO — and listen for the click. Click but no appliance → COM/NO screw
  terminals. No click → power or the active-low flag.
- **Flashing kept failing with "port busy."** Something still holds the USB
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
