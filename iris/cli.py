"""IRIS command-line interface.

Usage:
    iris                       start the assistant (opens the UI)
    iris --minimized           start without opening a browser window
    iris --headless            API server only, never touch the desktop
    iris --host 0.0.0.0        allow phones on the same network
    iris stop                  stop the running IRIS instance
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


def _stop(port: int | None = None) -> int:
    """Stop a running IRIS instance so the port is free to bind again.

    Only processes that look like IRIS are terminated: if something unrelated
    holds the port we report it rather than killing a stranger's process.
    """
    import os as _os

    import psutil

    from iris.app.core.config import settings

    target = port or settings.PORT
    me = _os.getpid()
    holders: list[tuple[int, str, bool]] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        if proc.pid == me:
            continue
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == target:
                    cmdline = " ".join(proc.info.get("cmdline") or [])
                    is_iris = "iris" in cmdline.lower()
                    holders.append((proc.pid, cmdline or (proc.info.get("name") or "?"), is_iris))
                    break
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

    if not holders:
        print(f"Nothing is listening on port {target}. IRIS is not running.")
        return 0

    stopped = 0
    for pid, cmdline, is_iris in holders:
        if not is_iris:
            print(f"Port {target} is held by PID {pid} ({cmdline}), which is not IRIS — leaving it alone.")
            continue
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            print(f"Stopped IRIS (PID {pid}).")
            stopped += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            print(f"Could not stop PID {pid}: {exc}")

    return 0 if stopped or holders else 1


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

    _doctor_cloud()
    return 0


def _doctor_cloud() -> None:
    """Live-fire each configured AI provider with a tiny request.

    This answers "why is IRIS answering offline?" in one command: every key in
    .env gets a real 4-token call and the exact provider error is printed
    verbatim instead of hiding in the server log.
    """
    import asyncio

    from iris.app.core.tls import use_system_trust_store
    use_system_trust_store()

    from iris.app.core import paths
    from iris.app.core.config import loaded_env_files, settings
    from iris.app.llm.gateway import default_model_gateway as gateway

    env_files = loaded_env_files()
    print()
    print(f"Config loaded from: {', '.join(env_files) if env_files else '(no .env found — looked in ' + str(paths.project_root() / '.env') + ')'}")

    names = settings.configured_providers()
    if not names:
        print("Cloud providers: none configured. Commands work offline; add a key to .env for conversations.")
        return

    print("Cloud provider live check:")

    async def probe() -> None:
        for name in names:
            provider = gateway.cloud_providers.get(name)
            if provider is None:
                continue
            try:
                res = await provider.generate("Reply with the single word: ok", max_tokens=4)
                latency = f"{res.latency_ms:.0f}ms" if res.latency_ms is not None else "ok"
                print(f"  ✓ {name} — model {res.model_name} answered in {latency}")
            except Exception as exc:  # noqa: BLE001 - report every failure verbatim
                print(f"  ✗ {name} — {exc}")
        await gateway.close()

    asyncio.run(probe())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iris", description="IRIS — personal desktop AI assistant")
    parser.add_argument("--minimized", action="store_true", help="don't open the browser window")
    parser.add_argument("--headless", action="store_true", help="API only; no browser, no tray")
    parser.add_argument("--host", help="bind address (0.0.0.0 enables phone access)")
    parser.add_argument("--port", type=int, help="port (default 8756)")

    sub = parser.add_subparsers(dest="command")
    auto = sub.add_parser("autostart", help="start IRIS at login")
    auto.add_argument("action", choices=["enable", "disable", "status"])
    stop = sub.add_parser("stop", help="stop the running IRIS instance")
    stop.add_argument("--port", type=int, dest="stop_port", help="port to free (default 8756)")
    sub.add_parser("token", help="print the API token for phone pairing")
    sub.add_parser("doctor", help="diagnose platform capabilities")

    args = parser.parse_args(argv)

    if args.command == "stop":
        return _stop(getattr(args, "stop_port", None))
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
