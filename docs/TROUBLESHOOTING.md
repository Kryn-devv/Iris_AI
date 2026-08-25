# Troubleshooting

## `ModuleNotFoundError: No module named 'uvicorn'`

The dependencies aren't installed in the interpreter you're using. If the
project has a `.venv` folder, target it directly instead of relying on shell
activation:

```powershell
# Windows
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m iris
```

```bash
# Linux / macOS
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m iris
```

Add `requirements-desktop.txt` too for app launching, screenshots, volume
control, global hotkeys and the tray icon.

## `[Errno 10048] only one usage of each socket address` (Windows) / `Address already in use` (Linux, macOS)

IRIS is **already running** — usually because autostart launched it at login.
Everything above the error in the log will show a healthy boot. Just open
<http://127.0.0.1:8756>.

To restart it (needed after editing `.env`):

```
python -m iris stop
python -m iris
```

`iris stop` only terminates processes that are actually IRIS; if something
unrelated holds the port it reports the owner instead of killing it.

Or right-click the tray icon and choose Quit. To run a second instance on
another port: `python -m iris --port 8757`.

## My API key is ignored / replies feel offline

Check the startup log. IRIS now reports its reasoning backend on every boot:

```
LLM: 2 provider(s) ready — openrouter, groq (routing in that order).
Config loaded from: C:\Users\you\Iris_AI\.env
```

If you instead see:

```
LLM: no API key detected — falling back to the offline engine.
```

then no key was found. Check, in order:

1. **The file is named exactly `.env`** — not `.env.txt`, not `env`. Windows
   File Explorer hides known extensions; enable *View → File name extensions*.
2. **No `#` in front of the key.** `#` means "ignored". Copying
   `.env.example` leaves every line commented out.
3. **No spaces around `=`.** Write `OPENROUTER_API_KEY=sk-or-v1-...`, not
   `OPENROUTER_API_KEY = sk-...`.
4. **No quotes** around the value.
5. **You restarted IRIS.** `.env` is read once at startup.

### Where `.env` is looked for

IRIS reads the first of these that exists, and later entries override earlier
ones:

1. `<project root>/.env` — next to the `iris` package. **Recommended.**
2. `<user config dir>/.env` — `%APPDATA%\IrisAI\.env` on Windows,
   `~/.config/IrisAI/.env` on Linux, `~/Library/Preferences/IrisAI/.env` on macOS.
3. `./.env` in the current working directory.

All three are resolved to absolute paths. This matters because autostart
launches IRIS from a Run key / LaunchAgent with no working directory — a
relative lookup would find nothing, so keys were silently ignored on every
boot. Fixed; `tests/core/test_env_resolution.py` pins the behaviour.

The exact paths checked on your machine:

```
python -m iris doctor
```

## Commands work but conversation doesn't

That's the intended fallback. Deterministic commands (`open youtube`,
`volume up`, `take a screenshot`) never touch the network and work with no key.
Open-ended conversation, code writing and content generation need a provider
key. Any one of these free tiers is enough:

| Provider | Key from | Env var |
|---|---|---|
| OpenRouter | <https://openrouter.ai/keys> | `OPENROUTER_API_KEY` |
| Groq | <https://console.groq.com/keys> | `GROQ_API_KEY` |
| Google AI Studio | <https://aistudio.google.com/apikey> | `GEMINI_API_KEY` |

## A leaked key

Anything pasted into a screenshot, chat or issue is compromised. Delete it at
the provider and create a new one. `.env` is in `.gitignore`, so it is never
committed — but a screenshot bypasses that entirely.

## Autostart

```
python -m iris autostart status
python -m iris autostart enable
python -m iris autostart disable
```

If IRIS doesn't come back after a reboot, confirm the registered command still
points at an interpreter that has the dependencies installed — moving or
deleting the `.venv` breaks it. Re-run `autostart enable` after moving the
project folder.

## Nothing at http://127.0.0.1:8756

- Confirm the log ends with `IRIS is ready at http://127.0.0.1:8756`.
- If you passed `--host 0.0.0.0`, use the LAN address it prints instead.
- A `[Errno 98]`/`[Errno 10048]` line above means an older instance owns the
  port; see the port section above.
