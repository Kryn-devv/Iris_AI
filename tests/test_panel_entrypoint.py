"""Tests for ``app.py``, the entry point generic Python panel eggs expect.

A panel egg ends its startup command with ``python /home/container/app.py`` and
gives the user no obvious way to see why nothing started. These tests cover the
decisions that file makes, because getting one wrong presents to the user as
"the panel says the server is online and the page never loads" — with nothing
in the console pointing at the cause.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parents[1] / "app.py"


def load_app_module():
    """Import ``app.py`` fresh, without it being on sys.path as a package."""
    spec = importlib.util.spec_from_file_location("iris_panel_entrypoint", APP_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def app_module(monkeypatch):
    # A clean environment: these tests are about what app.py decides, so any
    # value inherited from the machine running the suite would mask a bug.
    for name in (
        "HOST", "PORT", "ALLOW_LAN_ACCESS", "OPEN_BROWSER_ON_START",
        "TRAY_ENABLED", "SERVER_PORT", "PTERODACTYL_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    return load_app_module()


@pytest.fixture()
def captured_cli(monkeypatch, app_module):
    """Run main() but stop at the point it would start a server."""
    calls: dict = {}

    def fake_cli_main(argv=None):
        calls["argv"] = argv
        return 0

    fake = type(sys)("iris.cli")
    fake.main = fake_cli_main
    monkeypatch.setitem(sys.modules, "iris.cli", fake)
    return calls


class TestPanelPort:
    def test_uses_the_port_the_panel_assigned(self, monkeypatch, app_module, captured_cli):
        """Pterodactyl assigns the port; nothing in the repo can know it."""
        import os

        monkeypatch.setenv("SERVER_PORT", "7731")
        assert app_module.main() == 0
        assert os.environ["PORT"] == "7731"

    def test_an_explicit_port_wins_over_the_panel(self, monkeypatch, app_module, captured_cli):
        """Someone who wrote a port into .env meant it."""
        import os

        monkeypatch.setenv("PORT", "8756")
        monkeypatch.setenv("SERVER_PORT", "7731")
        app_module.main()
        assert os.environ["PORT"] == "8756"

    def test_a_junk_panel_port_is_ignored_not_passed_on(self, monkeypatch, app_module, captured_cli):
        """An empty or non-numeric SERVER_PORT must not become PORT="" — that
        fails much later, inside uvicorn, with a message about neither."""
        import os

        for junk in ("", "   ", "not-a-port", "7731/tcp"):
            monkeypatch.setenv("SERVER_PORT", junk)
            monkeypatch.delenv("PORT", raising=False)
            app_module.main()
            assert "PORT" not in os.environ, junk

    def test_pterodactyl_port_is_also_accepted(self, monkeypatch, app_module, captured_cli):
        import os

        monkeypatch.setenv("PTERODACTYL_PORT", "25580")
        app_module.main()
        assert os.environ["PORT"] == "25580"


class TestContainerDefaults:
    def test_binds_every_interface_so_the_port_mapping_reaches_it(
        self, monkeypatch, app_module, captured_cli
    ):
        import os

        app_module.main()
        assert os.environ["HOST"] == "0.0.0.0"
        # LAN access also switches token auth on, which is the point: a
        # container reachable from outside must not be reachable anonymously.
        assert os.environ["ALLOW_LAN_ACCESS"] == "true"

    def test_an_explicit_host_is_respected(self, monkeypatch, app_module, captured_cli):
        import os

        monkeypatch.setenv("HOST", "127.0.0.1")
        app_module.main()
        assert os.environ["HOST"] == "127.0.0.1"
        assert "ALLOW_LAN_ACCESS" not in os.environ

    def test_no_tray_and_no_browser_in_a_container(self, monkeypatch, app_module, captured_cli):
        import os

        app_module.main()
        assert os.environ["OPEN_BROWSER_ON_START"] == "false"
        assert os.environ["TRAY_ENABLED"] == "false"

    def test_runs_headless(self, monkeypatch, app_module, captured_cli):
        app_module.main()
        assert captured_cli["argv"] == ["--headless"]

    def test_returns_the_cli_exit_code(self, monkeypatch, app_module):
        """The panel decides whether to restart from the exit code, so
        swallowing a failure would make a crash look like a clean stop."""
        fake = type(sys)("iris.cli")
        fake.main = lambda argv=None: 3
        monkeypatch.setitem(sys.modules, "iris.cli", fake)
        assert app_module.main() == 3


def test_the_file_panels_look_for_exists_at_the_repo_root():
    """The egg default is PY_FILE=app.py at /home/container. If this file ever
    moves, the panel's only symptom is "can't open file 'app.py'"."""
    assert APP_PY.is_file()
