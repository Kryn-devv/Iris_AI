"""Entry point for panel hosts (Pterodactyl and friends).

Generic Python eggs end their startup command with

    python /home/container/${PY_FILE}

and default ``PY_FILE`` to ``app.py``. Without this file the panel starts
nothing and the console shows only "can't open file 'app.py'", so the whole
deployment has to be diagnosed through a startup command the user did not
write. This file exists so the egg's defaults simply work: point the panel at
this repository, press start, done.

It is a thin shim over ``python -m iris --headless``, plus the two things a
container knows that a laptop does not:

* the port is assigned by the panel, not chosen by the user, and it can change
  when the allocation changes;
* there is no desktop, so the tray icon and the "open a browser" step must be
  off before anything tries them.

Run locally with ``python -m iris`` as usual — nothing here changes that path.
"""

from __future__ import annotations

import os
import sys


def _port_from_panel() -> str | None:
    """The port the panel assigned, if it assigned one.

    Pterodactyl exports ``SERVER_PORT``; several other panels export ``PORT``.
    An explicit ``PORT`` in ``.env`` still wins over both — see below — because
    someone who wrote a port down means it.
    """
    for name in ("SERVER_PORT", "PTERODACTYL_PORT"):
        value = (os.environ.get(name) or "").strip()
        if value.isdigit():
            return value
    return None


def main() -> int:
    # A container has no desktop. Setting these before iris.cli is imported
    # keeps anything from reaching for a tray icon or a browser that is not
    # there; --headless below does the same, and doing both is deliberate so a
    # future change to one does not quietly re-enable the other.
    os.environ.setdefault("OPEN_BROWSER_ON_START", "false")
    os.environ.setdefault("TRAY_ENABLED", "false")

    argv = ["--headless"]

    # Bind on every interface, or the panel's port mapping reaches nothing and
    # the server looks dead while running perfectly.
    if not os.environ.get("HOST"):
        os.environ["HOST"] = "0.0.0.0"
        os.environ["ALLOW_LAN_ACCESS"] = "true"

    # `.env` wins, then the panel's allocation. Reading the panel's port means
    # a changed allocation does not need `.env` edited to match, which is the
    # kind of mismatch that presents as "the panel says online, the page never
    # loads".
    if not os.environ.get("PORT"):
        panel_port = _port_from_panel()
        if panel_port:
            os.environ["PORT"] = panel_port
            print(f"[iris] using the port this panel assigned: {panel_port}", flush=True)

    from iris.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
