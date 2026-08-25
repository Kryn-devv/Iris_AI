# Using IRIS from your phone

## Option 1 — Same Wi-Fi (fastest)
1. In `.env` set `ALLOW_LAN_ACCESS=true`, restart IRIS.
2. On the computer, open the IRIS UI → ⚙ Settings → **Phone access** → *Show pairing info*.
3. Scan the QR (or type the URL) on your phone. The token in the URL is remembered by the phone's browser.
4. You now have the full IRIS UI — hologram, voice (Chrome for Android supports the mic), everything.

Tip: `iris token` prints the token again anytime.

## Option 2 — From anywhere via Telegram (free)
1. In Telegram, talk to **@BotFather** → `/newbot` → copy the token.
2. Get your numeric user id from **@userinfobot**.
3. In `.env`:
   ```
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_ALLOWED_USER_IDS=123456789
   ```
4. Restart IRIS and message your bot: "open youtube", "take a screenshot",
   "remind me at 6 pm to call home", "make a ppt about volcanoes" — files it
   creates are sent back to your phone. Voice notes work when a server STT
   engine (e.g. `pip install faster-whisper`) is installed.

Only the user ids you list can talk to your computer. Everyone else is ignored.
