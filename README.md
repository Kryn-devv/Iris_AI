<div align="center">

# ◉ IRIS

### Your personal desktop AI assistant

**Voice-controlled · Fully automated · Free-first · Private by design**

*"Open YouTube." "Take a screenshot." "Remind me in 20 minutes." "Make a ppt about space travel." — done.*

</div>

---

IRIS is a full desktop AI assistant that lives on your machine. It opens apps and websites, controls volume and windows, takes screenshots, searches the web, checks weather and news, sets reminders that survive reboots, builds PowerPoint decks and Word documents, writes code, and talks with you — by text or voice, from your desktop or your phone.

It's built **free-first**: every command works with *zero* API keys thanks to a deterministic command engine, and plugging in any single free AI key (OpenRouter, Groq, Google AI Studio, …) unlocks full conversations and rich content generation. There is no self-hosted LLM to install, no GPU needed, nothing to pay for.

---

## ✨ What can it do?

| Say / type… | IRIS… |
|---|---|
| "open youtube" / "open notepad" | launches websites & apps (60+ aliases) |
| "play lo-fi beats on youtube" | opens the search/results directly |
| "search for keyboards on amazon" | site-specific searches (YouTube, Google, Amazon, Maps…) |
| "volume up" / "set volume to 40" / "mute" | controls system volume |
| "pause" / "next song" | media keys |
| "take a screenshot" | captures & saves to `~/Iris/screenshots` |
| "type hello world" / "press ctrl+s" | drives keyboard & mouse |
| "what's in my clipboard" / "copy X to clipboard" | clipboard |
| "switch to chrome" / "minimize spotify" | window management |
| "what's the weather in Mumbai" | free weather, no key (open-meteo) |
| "news about AI" | live RSS headlines |
| "remind me in 20 min to stretch" / "set a timer for 5 minutes" | persistent reminders & timers with voice + notification |
| "every weekday at 9 remind me to check email" | recurring routines |
| "make a ppt about renewable energy" | builds a real `.pptx` deck (or a beautiful HTML deck with zero deps) |
| "write a document about the French revolution" | Word / Markdown documents |
| "create a spreadsheet of monthly expenses" | Excel / CSV |
| "write a python script that renames files" | code straight into `~/Iris/projects` |
| "what is 25 multiplied by 47" / "25 को 40 से गुणा करो" | instant math (English, Hindi & Hinglish) |
| "who is Alan Turing" / "wiki black holes" | Wikipedia summaries |
| "search the web for best laptops 2026" | DuckDuckGo search, no key |
| "lock my pc" / "shutdown my computer" | power controls (guarded) |
| "remember my project budget is 5000" | long-term memory |
| "start iris when my pc boots" | registers itself as a startup app |

…and anything else becomes a conversation with a free AI model, which can chain any of the 65+ tools itself.

## 🏠 ESP32, home automation & the robot

IRIS drives your WiFi hardware — relay boards for lights/fans/sockets and an
L298N motor base for the robot — over plain HTTP on your LAN:

```
add device kitchen light at 192.168.1.73 as relay
turn on the kitchen light        ·  light chalu karo
fan band karo                    ·  toggle the socket
add device robot at 192.168.1.74 as motor
robot forward · move the robot left · stop the robot
```

Flash the bundled universal firmware (`firmware/esp32-iris-node/`) or keep
your existing sketches and map their URLs per device. Full wiring and setup
guide: **[docs/ESP32.md](docs/ESP32.md)**. Registered devices also show up in
the settings drawer with live online state and toggle buttons.

## 🗣 Languages & voice

Talk to IRIS in **English, Hindi or Hinglish** — it detects the language and
replies (and speaks) in kind, always with a **female voice**: Zira/Aria/Heera
on Windows, Samantha/Lekha on macOS, Swara for Hindi via edge-tts, and
matching browser voices. `delhi ka mausam kaisa hai`, `kitne baje hain`,
`screenshot lo`, `awaaz badhao`, `notepad kholo` all work offline.

## 🖥 The interface

A minimalist dark UI with a **3D holographic particle sphere** at its heart — it drifts while idle, blooms when listening, swirls while thinking and pulses as it speaks. Live activity ticker, confirmation dialogs for risky actions, a settings drawer with provider/voice/tool status, and one-tap phone pairing with a QR code.

Voice works **in the browser with zero installs** (Chrome/Edge speech recognition + speech synthesis), including hands-free **"Hey Iris…"** wake-word mode. Install offline engines (`faster-whisper`, `pyttsx3`, `piper`) any time and IRIS switches to them automatically.

## 🚀 Quick start

```bash
git clone https://github.com/Kryn-devv/Iris_AI.git
cd Iris_AI
```

**Windows** — double-click `scripts\install-windows.bat`, done.

**macOS** — double-click **`scripts/Install IRIS on macOS.command`**, or from a
terminal:
```bash
bash scripts/install-macos.sh
```
> Don't double-click `install-macos.sh` itself. Finder does not execute a `.sh`
> — it opens it in whatever app owns the extension, so with an editor installed
> the installer just appears as source code and nothing runs. The `.command`
> file above is the same script in a form Finder does run.

**Linux**
```bash
bash scripts/install-linux.sh
```

**Manual (any OS)**
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt              # core (always works)
pip install -r requirements-desktop.txt      # desktop control, voice, ppt/doc/xlsx (recommended)
python -m iris
```

The UI opens at **http://127.0.0.1:8756**. That's it — no keys required.

### Unlock full AI conversations (optional, still free)

Copy `.env.example` → `.env` and add **any one** free key:

| Provider | Get a free key | 
|---|---|
| OpenRouter (`:free` models) | https://openrouter.ai/keys |
| Groq (fastest) | https://console.groq.com/keys |
| Google AI Studio | https://aistudio.google.com/apikey |
| Cerebras | https://cloud.cerebras.ai |
| Mistral | https://console.mistral.ai |
| Together | https://api.together.ai |
| GitHub Models | any GitHub PAT |
| Hugging Face | https://huggingface.co/settings/tokens |

IRIS routes across every configured provider in your preferred order, with automatic failover and cool-down when one rate-limits — and always falls back to its offline engine, so it never breaks. Any other OpenAI-compatible gateway works too via `OPENAI_COMPAT_BASE_URL`.

### Start with your computer

```bash
iris autostart enable      # or just tell it: "start iris when my pc boots"
```

### Use it from your phone

- **Same Wi-Fi:** set `ALLOW_LAN_ACCESS=true` in `.env`, restart, open *Settings ▸ Phone access* in the UI and scan the QR. A bearer token protects remote access automatically.
- **From anywhere (free):** create a Telegram bot with **@BotFather**, set `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN=…` and your `TELEGRAM_ALLOWED_USER_IDS=…`. Chat with your computer from any network — voice notes included. Generated files are sent right back to your phone.

### Useful commands

```bash
iris                  # start (opens the UI)
iris --minimized      # start silently (what autostart uses)
iris --headless       # API only, e.g. on a server
iris doctor           # see which optional capabilities are installed/missing
iris token            # print the phone-pairing token
```

## 🧠 How it works

```
 you (text · voice · phone · telegram)
   │
   ▼
┌────────────────────────── KERNEL PIPELINE ──────────────────────────┐
│ 1 wake-word strip        "hey iris, …"                             │
│ 2 memory commands        remember / recall / forget                │
│ 3 small talk             greetings, identity, jokes    (offline)   │
│ 4 deterministic NLU      50+ intent rules → direct tool dispatch   │
│                          "open youtube" runs in milliseconds,      │
│                          offline, no model call at all             │
│ 5 LLM agent loop         free-provider router with fallback chain  │
│                          + function calling over all 65+ tools     │
└──────────────────┬──────────────────────────────┬──────────────────┘
                   ▼                              ▼
        ┌──────────────────┐          ┌───────────────────────┐
        │   TOOL REGISTRY  │          │     MODEL GATEWAY     │
        │ desktop · web ·  │          │ openrouter→groq→gemini│
        │ content · files  │          │ →cerebras→…→offline   │
        │ system · voice · │          │ circuit breaker/cooldn│
        │ automation       │          └───────────────────────┘
        └────────┬─────────┘
                 ▼
   ┌──────────────────────────┐   every tool call passes through
   │      SECURITY LAYER      │   • risk-graded permissions
   │ permission manager ·     │   • filesystem sandbox
   │ path sandbox · command   │   • destructive-command denylist
   │ policy                   │   • confirmation round-trips
   └──────────────────────────┘
```

**Event bus → everywhere.** Planning, tool activity, voice state and reminders stream over a WebSocket to the UI (hologram + ticker), the voice pipeline and the Telegram bridge simultaneously.

**Persistent by design.** Reminders, memories, tasks and tool audit logs live in SQLite under your user data directory — reminders set today still fire after a reboot (IRIS runs at startup, after all).

## 🔒 Security model

IRIS can drive your whole desktop, so it's guarded in depth:

- **Risk-graded permissions** — every tool declares its level (`READ` → `DESKTOP_ACTION` → `CONFIRM_REQUIRED` → `HIGH_RISK_ACTION`). Shutdown/restart require *both* an opt-in flag and a confirmation click. Deletes always confirm.
- **Filesystem sandbox** — file tools only touch your workspace + standard user folders; `~/.ssh`, key files and `.env` are pattern-blocked; symlinks are resolved before checks.
- **Command policy** — the shell tool is **off by default**; even when enabled, a hard denylist refuses destructive patterns (`rm -rf /`, fork bombs, `format C:`, `curl | sh`, …) no matter what any model says.
- **Remote access auth** — LAN/phone access enforces an auto-generated bearer token + rate limiting; the Telegram bridge only answers allow-listed user IDs.
- **No key leakage** — credentials are redacted from logs and never appear in status endpoints.
- **Local-first** — your files, screen and audio stay on your machine unless *you* configure a cloud provider; even then only the chat text is sent.

## 📂 Project structure

```
iris/
├── cli.py                  # `iris` command: run/autostart/token/doctor
└── app/
    ├── main.py             # FastAPI app, auth middleware, lifespan
    ├── core/               # config · logging · security · bus · auth · paths · platform probing
    ├── nlu/                # deterministic intent engine + 50-rule catalogue
    ├── agent/              # layered kernel · smalltalk · prompts · task state
    ├── llm/                # cloud provider (OpenAI-compatible) · gateway/router · offline mock
    ├── voice/              # TTS/STT engine chains · voice service · speak tool
    ├── tools/
    │   ├── desktop/        # apps · websites · input · clipboard · windows · screenshot · notify · media · power
    │   ├── web/            # search · fetch · wikipedia · weather · news
    │   ├── content/        # presentations · documents · spreadsheets · code writer
    │   ├── files/          # sandboxed file manager
    │   ├── system/         # processes · guarded shell · network
    │   ├── automation/     # reminders · timers · routines
    │   └── builtin/        # calculator · time · system info · strings · units
    ├── services/           # scheduler · telegram bridge
    ├── desktop/            # autostart (Win/Linux/macOS) · tray icon
    ├── memory/             # working/conversation/long-term/project memory
    ├── language/           # language detection · Hindi/Hinglish normalization
    ├── api/routes/         # chat · ws/sse events · voice · system · tasks · tools · memory · llm
    └── static/             # dark UI · 3D hologram sphere (pure canvas, zero deps)
tests/                      # 550+ tests
```

## 🧪 Tests

```bash
python -m pytest -q
```

## 🛠 Troubleshooting

- **`iris doctor`** shows exactly which optional capability is missing and the one-line install for it.
- On Linux, desktop control works best with: `sudo apt install wmctrl xclip scrot espeak-ng ffmpeg playerctl`
- Voice not working in the browser? Use Chrome/Edge (Web Speech API) or install `pyttsx3` + `SpeechRecognition` for server-side voice.
- Behind a strict network? IRIS still does everything except cloud conversations and live web tools.

---

<div align="center">
<sub>IRIS runs on your machine, for you. ◉</sub>
</div>
> **Something not working?** See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — covers missing dependencies, the port-already-in-use error, and `.env` keys being ignored.

