# IRIS — Developer Guide

Personal desktop AI assistant. FastAPI backend + vanilla-JS dark UI with a canvas hologram. Python 3.11+.

## Run
```bash
pip install -r requirements.txt          # core; requirements-desktop.txt for extras
python -m iris                           # UI at http://127.0.0.1:8756
python -m pytest -q                      # full test suite
```

## Architecture (read in this order)
- `iris/app/agent/kernel.py` — layered pipeline: wake-word → memory → smalltalk → NLU → LLM agent loop.
- `iris/app/nlu/rules.py` — deterministic intent catalogue; first matching rule wins. Add new command phrasings here.
- `iris/app/tools/` — every capability is a `BaseTool` (`tools/base.py`). Modules export `get_tools()`; register in `tools/loader.py` `TOOL_MODULES`.
- `iris/app/llm/gateway.py` — free-provider router (OpenRouter/Groq/Gemini/…) with circuit breaker; offline `mock.py` is the guaranteed fallback.
- `iris/app/core/security.py` — permission levels, `PathSandbox` (all file access), `CommandPolicy` (shell denylist). Never bypass these in tools.
- `iris/app/core/bus.py` — pub/sub feeding the UI WebSocket, voice and Telegram.

## Conventions
- Tools: return dicts with a `"speech"` key (short spoken sentence); raise `ToolError` for clean failures; optional deps via `platform_info.try_import` — never top-level imports of optional packages; blocking work via `await self.to_thread(...)`.
- Everything must boot and pass tests on headless Linux with zero optional deps installed.
- Writable paths only via `iris/app/core/paths.py`; user files via `default_path_sandbox.resolve()`.
- Settings: add to `core/config.py` + document in `.env.example`.

## Test layout
`tests/nlu` (intent routing), `tests/agent` (kernel layers, confirmations), `tests/core` (security/bus/scheduler), `tests/tools` (per tool suite, all mocked — no real launches/network), `tests/voice`.
