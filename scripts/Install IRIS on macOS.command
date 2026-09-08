#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
#  Double-clickable installer for macOS.
#
#  WHY THIS FILE EXISTS
#  Finder does not execute a .sh on double-click — it opens it in whatever
#  app owns the extension, which for anyone with an editor installed means
#  the installer appears as source code and nothing happens. macOS DOES
#  run a .command in Terminal on double-click, so this is the one-liner
#  wrapper that makes the obvious action the working one.
#
#  Terminal users lose nothing: scripts/install-macos.sh is still there and
#  is still what this calls.
# ══════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"
exec bash ./install-macos.sh
