"""IRIS command-line interface.

Usage:
    iris                       start the assistant (opens the UI)
    iris --minimized           start without opening a browser window
    iris --headless            API server only, never touch the desktop
    iris --host 0.0.0.0        allow phones on the same network
    iris autostart enable      start IRIS when the computer boots
    iris autostart disable
    iris autostart status
    iris token                 print the API token for phone pairing
    iris doctor                show platform capabilities and missing extras
"""

from __future__ import annotations

import argparse
import os
import sys


def _run_server(args: argparse.Namespace) -> None:
    if args.minimized:
        os.environ["OPEN_BROWSER_ON_START"] = "false"
        os.environ.setdefault("START_MINIMIZED", "true")
    if args.headless:
        os.environ["OPEN_BROWSER_ON_START"] = "false"
        os.environ["TRAY_ENABLED"] = "false"
        os.environ["DESKTOP_MODE"] = "headless"
    if args.host:
        os.environ["HOST"] = args.host
        if args.host in ("0.0.0.0", "::"):
            os.environ["ALLOW_LAN_ACCESS"] = "true"
    if args.port:
        os.environ["PORT"] = str(args.port)

    import uvicorn

    from iris.app.core.config import reload_settings

    settings = reload_settings()

    tray = None
    if settings.TRAY_ENABLED and not args.headless:
        try:
            from iris.app.desktop.tray import TrayIcon

            tray = TrayIcon(on_quit=lambda: os._exit(0))
            tray.start()
        except Exception:  # noqa: BLE001 - tray is cosmetic
            tray = None

    try:
        uvicorn.run(
            "iris.app.main:app",
            host=settings.bind_host,
            port=settings.PORT,
            log_level="warning",
        )
    finally:
        if tray is not None:
            tray.stop()


def _autostart(action: str) -> int:
    from iris.app.desktop import autostart

    if action == "enable":
        info = autostart.enable()
        print(f"Autostart enabled: {info['command']}")
    elif action == "disable":
        autostart.disable()
        print("Autostart disabled.")
    else:
        info = autostart.status()
        print(f"Autostart: {'enabled' if info['enabled'] else 'disabled'} ({info['os']})")
    return 0


def _token() -> int:
    from iris.app.core.auth import ensure_token

    print(ensure_token())
    return 0


def _doctor() -> int:
    from iris.app.core.platform_info import platform_report

    report = platform_report().to_dict()
    print(f"IRIS doctor — {report['os']} {report['os_release']} ({report['architecture']})")
    print(f"Python {report['python_version']} · display: {'yes' if report['has_display'] else 'no'}")
    print()
    print("Available capabilities:")
    for name in report["available"]:
        print(f"  ✓ {name}")
    print()
    print("Missing capabilities (install to unlock):")
    for name in report["missing"]:
        cap = report["capabilities"][name]
        hint = f" — {cap['install_hint']}" if cap.get("install_hint") else ""
        print(f"  ✗ {name}{hint}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iris", description="IRIS — personal desktop AI assistant")
    parser.add_argument("--minimized", action="store_true", help="don't open the browser window")
    parser.add_argument("--headless", action="store_true", help="API only; no browser, no tray")
    parser.add_argument("--host", help="bind address (0.0.0.0 enables phone access)")
    parser.add_argument("--port", type=int, help="port (default 8756)")

    sub = parser.add_subparsers(dest="command")
    auto = sub.add_parser("autostart", help="start IRIS at login")
    auto.add_argument("action", choices=["enable", "disable", "status"])
    sub.add_parser("token", help="print the API token for phone pairing")
    sub.add_parser("doctor", help="diagnose platform capabilities")

    args = parser.parse_args(argv)

    if args.command == "autostart":
        return _autostart(args.action)
    if args.command == "token":
        return _token()
    if args.command == "doctor":
        return _doctor()

    _run_server(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
