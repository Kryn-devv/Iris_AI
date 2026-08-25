#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  IRIS one-shot installer for Linux / macOS
# ══════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null; then
    echo "Python 3.11+ is required."; exit 1
fi

echo "[1/4] Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip -q

echo "[2/4] Installing IRIS core..."
pip install -r requirements.txt -q

echo "[3/4] Installing desktop, voice and content extras (best-effort)..."
pip install -r requirements-desktop.txt -q || echo "  (some extras skipped — run 'iris doctor' to see what's missing)"

if [[ "$(uname -s)" == "Linux" ]]; then
    echo "  Tip: for full desktop control install system packages:"
    echo "       sudo apt install wmctrl xclip scrot espeak-ng ffmpeg  (Debian/Ubuntu)"
fi

echo "[4/4] Registering autostart..."
python -m iris autostart enable || true

echo
echo "Done! Starting IRIS now..."
echo "(Later, just run: .venv/bin/python -m iris)"
python -m iris
