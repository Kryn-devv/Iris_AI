#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  IRIS one-shot installer for macOS
# ══════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null; then
    echo "Python 3.11+ is required. Install it with: brew install python@3.12"
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Python 3.11+ is required — found $(python3 --version 2>&1)."
    echo "Install a newer one with: brew install python@3.12"
    exit 1
fi

echo "[1/4] Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip -q

echo "[2/4] Installing IRIS core..."
pip install -r requirements.txt -q

echo "[3/4] Installing desktop, voice and content extras (best-effort)..."
pip install -r requirements-desktop.txt -q || echo "  (some extras skipped — run 'iris doctor' to see what's missing)"

echo "[4/4] Registering autostart..."
python -m iris autostart enable || true

echo
echo "macOS permission checklist (System Settings > Privacy & Security):"
echo "  • Accessibility — add your terminal (Terminal/iTerm) so IRIS can"
echo "    type, click and manage windows."
echo "  • Microphone    — allow it for voice commands."
echo "  • Automation    — allow control of System Events (asked on first"
echo "    shutdown/restart/lock)."
echo "  • Notifications — enable them for your terminal so toasts appear."
echo
echo "Done! Starting IRIS now..."
echo "(Later, just run: .venv/bin/python -m iris)"
python -m iris
