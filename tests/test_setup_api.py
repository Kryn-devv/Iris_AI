"""Pasting a key into the app instead of editing .env in an editor.

Two things are being protected here. One is the user's configuration file,
which a program rewriting it could quietly destroy. The other is the key
itself, which must never come back out of the API it went into.
"""

from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient

import iris.app.api.routes.setup as setup_mod
from iris.app.core import envfile
from iris.app.core.config import settings
from iris.app.main import app

KEY = "AIzaSyTESTKEY0123456789abcdef"


#: Everything the endpoint is allowed to change on the live settings object.
_TOUCHED = (
    "USER_NAME", "VISION_MODEL", "LLM_PROVIDER_ORDER",
    "GEMINI_API_KEY", "GEMINI_MODEL",
    "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
    "GROQ_API_KEY", "GROQ_MODEL",
)


@pytest.fixture(autouse=True)
def restore_global_settings():
    """Put the process back exactly as it was found.

    The endpoint deliberately mutates the shared settings object so a pasted
    key works without a restart — which means monkeypatch cannot undo it, and
    a leaked fake key would silently change how every later test behaves.
    """
    from iris.app.llm.gateway import default_model_gateway

    before = {name: getattr(settings, name) for name in _TOUCHED}
    try:
        yield
    finally:
        for name, value in before.items():
            setattr(settings, name, value)
        default_model_gateway.rebuild()


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """A throwaway .env that every write in this module is aimed at."""
    path = tmp_path / ".env"
    path.write_text(
        "# my own notes\n"
        "APP_NAME=IRIS\n"
        "# GEMINI_API_KEY=\n"
        "LOG_LEVEL=DEBUG\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(envfile, "target_path", lambda: path)
    return path


@pytest.fixture()
def client(env_file, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "USER_NAME", "")
    with TestClient(app) as c:
        yield c


# ============================================================ the env writer
class TestEnvFile:
    def test_it_keeps_everything_it_did_not_come_for(self, env_file):
        envfile.update_env({"USER_NAME": "Prajjwal"}, path=env_file)
        text = env_file.read_text()
        assert "# my own notes" in text
        assert "APP_NAME=IRIS" in text
        assert "LOG_LEVEL=DEBUG" in text
        assert "USER_NAME=Prajjwal" in text

    def test_a_commented_key_is_uncommented_in_place(self, env_file):
        """Every key in .env.example starts commented out; that is the point."""
        envfile.update_env({"GEMINI_API_KEY": KEY}, path=env_file)
        lines = env_file.read_text().splitlines()
        assert f"GEMINI_API_KEY={KEY}" in lines
        assert "# GEMINI_API_KEY=" not in lines
        assert lines.index(f"GEMINI_API_KEY={KEY}") == 2      # where it already was

    def test_writing_twice_replaces_rather_than_duplicates(self, env_file):
        envfile.update_env({"USER_NAME": "A"}, path=env_file)
        envfile.update_env({"USER_NAME": "B"}, path=env_file)
        text = env_file.read_text()
        assert text.count("USER_NAME=") == 1 and "USER_NAME=B" in text

    def test_the_added_header_appears_once_however_often_it_runs(self, env_file):
        for i in range(4):
            envfile.update_env({f"NEW_KEY_{i}": str(i)}, path=env_file)
        assert env_file.read_text().count("# Added by IRIS") == 1

    def test_none_removes_a_key(self, env_file):
        envfile.update_env({"LOG_LEVEL": None}, path=env_file)
        assert "LOG_LEVEL" not in env_file.read_text()

    def test_secrets_are_owner_only(self, env_file):
        envfile.update_env({"GEMINI_API_KEY": KEY}, path=env_file)
        mode = stat.S_IMODE(os.stat(env_file).st_mode)
        assert mode == 0o600, oct(mode)

    @pytest.mark.parametrize("value,expected", [
        ("plain", "plain"),
        ("has space", '"has space"'),
        ('has"quote', '"has\\"quote"'),
        ("hash#inside", '"hash#inside"'),
        ("", ""),
    ])
    def test_quoting_round_trips(self, value, expected):
        assert envfile.quote(value) == expected

    def test_a_newline_cannot_smuggle_in_another_setting(self, env_file):
        """The one way a pasted value could write configuration of its own."""
        envfile.update_env({"USER_NAME": "x\nREQUIRE_AUTH=false"}, path=env_file)
        text = env_file.read_text()
        assert "\nREQUIRE_AUTH" not in text
        assert envfile.read_values(["REQUIRE_AUTH"], path=env_file) == {}

    def test_an_unwritable_target_raises_rather_than_lying(self, tmp_path, monkeypatch):
        """A read-only directory does not stop root, so fail the syscall itself."""
        target = tmp_path / ".env"
        target.write_text("A=1\n")

        def refuse(*a, **k):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(envfile.tempfile, "mkstemp", refuse)
        with pytest.raises(envfile.EnvWriteError):
            envfile.update_env({"B": "2"}, path=target)
        assert target.read_text() == "A=1\n"           # and the original survived

    def test_a_crash_mid_write_leaves_the_original_intact(self, tmp_path, monkeypatch):
        """Losing a whole config to a half-finished save is the worse bug."""
        target = tmp_path / ".env"
        target.write_text("A=1\nB=2\n")
        real_replace = envfile.os.replace

        def explode(src, dst):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(envfile.os, "replace", explode)
        with pytest.raises(envfile.EnvWriteError):
            envfile.update_env({"A": "9"}, path=target)
        assert target.read_text() == "A=1\nB=2\n"
        # and nothing half-written was left lying beside it
        monkeypatch.setattr(envfile.os, "replace", real_replace)
        assert [p.name for p in tmp_path.iterdir()] == [".env"]

    def test_read_values_ignores_commented_lines(self, env_file):
        found = envfile.read_values(["APP_NAME", "GEMINI_API_KEY"], path=env_file)
        assert found == {"APP_NAME": "IRIS"}

    @pytest.mark.parametrize("secret,expected", [
        ("", ""), ("short", "•••••"), ("AIzaSy0123456789", "AIza••••••6789"),
    ])
    def test_masking_shows_enough_to_recognise_and_not_enough_to_use(self, secret, expected):
        assert envfile.mask(secret) == expected


# ================================================================= the API
class TestStatus:
    def test_it_says_what_is_missing_without_leaking_anything(self, client):
        body = client.get("/api/v1/setup/status").json()
        assert body["configured"] is False
        assert [p["name"] for p in body["providers"]] == ["gemini", "openrouter", "groq"]
        assert body["works_without_key"] is True
        assert body["env_writable"] is True
        assert "api_key" not in str(body)

    def test_a_configured_key_comes_back_masked_only(self, client, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", KEY)
        body = client.get("/api/v1/setup/status").json()
        gemini = next(p for p in body["providers"] if p["name"] == "gemini")
        assert gemini["configured"] is True
        assert KEY not in str(body)
        assert gemini["masked"].startswith("AIza") and "•" in gemini["masked"]


class TestSaving:
    def test_a_name_on_its_own(self, client, env_file):
        body = client.post("/api/v1/setup", json={"user_name": "Prajjwal"}).json()
        assert body["ok"] and body["user_name"] == "Prajjwal"
        assert "USER_NAME=Prajjwal" in env_file.read_text()
        assert settings.USER_NAME == "Prajjwal"        # live, without a restart

    def test_a_key_is_applied_without_a_restart(self, client, env_file):
        body = client.post(
            "/api/v1/setup",
            json={"provider": "gemini", "api_key": KEY, "verify": False},
        ).json()
        assert body["ok"] and body["configured"] is True
        assert body["restart_required"] is False
        assert settings.GEMINI_API_KEY == KEY
        assert f"GEMINI_API_KEY={KEY}" in env_file.read_text()

    def test_the_key_is_never_echoed_back(self, client):
        res = client.post(
            "/api/v1/setup",
            json={"provider": "gemini", "api_key": KEY, "verify": False},
        )
        assert KEY not in res.text

    def test_the_new_provider_answers_the_next_message(self, client):
        """A key pasted just now should not sit behind one that was already there."""
        client.post("/api/v1/setup",
                    json={"provider": "groq", "api_key": "gsk_x", "verify": False})
        assert settings.LLM_PROVIDER_ORDER[0] == "groq"

    def test_gemini_also_becomes_the_camera_s_eyes(self, client):
        """One key, not two — nobody knows to go looking for VISION_MODEL."""
        client.post("/api/v1/setup",
                    json={"provider": "gemini", "api_key": KEY, "verify": False})
        assert settings.VISION_MODEL == "gemini-flash-latest"

    def test_opting_out_of_that(self, client, monkeypatch):
        monkeypatch.setattr(settings, "VISION_MODEL", None)
        client.post("/api/v1/setup", json={
            "provider": "gemini", "api_key": KEY, "verify": False, "use_for_vision": False})
        assert settings.VISION_MODEL is None

    def test_an_empty_request_is_refused(self, client):
        assert client.post("/api/v1/setup", json={}).status_code == 400

    def test_an_unknown_provider_names_the_ones_that_work(self, client):
        res = client.post("/api/v1/setup",
                          json={"provider": "skynet", "api_key": "x", "verify": False})
        assert res.status_code == 400
        assert "gemini" in res.json()["detail"]


class TestVerification:
    """A key that is merely stored looks identical to one that works."""

    @staticmethod
    def _provider_raising(message):
        from iris.app.llm.base import LLMProviderError

        class Fake:
            async def generate(self, *a, **k):
                raise LLMProviderError(message)

            async def close(self):
                return None

        return lambda name, creds: Fake()

    def test_a_refused_key_is_reported_in_words(self, client, env_file, monkeypatch):
        monkeypatch.setattr(setup_mod, "build_provider",
                            self._provider_raising("gemini: HTTP 401 — API key not valid"))
        res = client.post("/api/v1/setup", json={"provider": "gemini", "api_key": "wrong"})
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "refused that key" in detail
        assert "AIza" in detail, "the key prefix is case-sensitive"
        # and nothing was written on the way out
        assert "wrong" not in env_file.read_text()

    def test_rate_limiting_is_not_reported_as_a_bad_key(self, client, monkeypatch):
        monkeypatch.setattr(setup_mod, "build_provider",
                            self._provider_raising("gemini: HTTP 429 — quota exceeded"))
        detail = client.post("/api/v1/setup",
                             json={"provider": "gemini", "api_key": KEY}).json()["detail"]
        assert "rate-limiting" in detail and "refused" not in detail

    def test_being_offline_is_not_reported_as_a_bad_key(self, client, monkeypatch):
        monkeypatch.setattr(setup_mod, "build_provider",
                            self._provider_raising("gemini: connection failed (ConnectError)"))
        detail = client.post("/api/v1/setup",
                             json={"provider": "gemini", "api_key": KEY}).json()["detail"]
        # The wording may change; what must not is which thing gets blamed.
        assert "reach" in detail.lower()
        assert "model" not in detail.lower()

    @staticmethod
    def _provider_where_only(working_model):
        """A provider that has exactly one model and 404s on every other."""
        from iris.app.llm.base import LLMProviderError

        asked = []

        class Fake:
            def __init__(self, model):
                self.model = model

            async def generate(self, *a, **k):
                asked.append(self.model)
                if self.model != working_model:
                    raise LLMProviderError(
                        f"gemini: HTTP 404 — models/{self.model} is not found"
                    )
                return type("R", (), {"content": "ok"})()

            async def close(self):
                return None

        return (lambda name, creds: Fake(creds.get("model"))), asked

    def test_a_retired_default_model_falls_back_instead_of_blaming_the_key(
        self, client, env_file, monkeypatch
    ):
        """Providers retire model names; a first-run key must not pay for it."""
        build, asked = self._provider_where_only("gemini-2.0-flash")
        monkeypatch.setattr(setup_mod, "build_provider", build)

        res = client.post("/api/v1/setup", json={"provider": "gemini", "api_key": KEY})

        assert res.status_code == 200, res.json()
        assert res.json()["model"] == "gemini-2.0-flash"
        assert asked[0] == "gemini-flash-latest", "the default is still tried first"
        assert "GEMINI_MODEL=gemini-2.0-flash" in env_file.read_text()

    def test_a_model_you_chose_yourself_is_never_swapped(self, client, monkeypatch):
        build, asked = self._provider_where_only("gemini-2.0-flash")
        monkeypatch.setattr(setup_mod, "build_provider", build)

        res = client.post("/api/v1/setup", json={
            "provider": "gemini", "api_key": KEY, "model": "gemini-3-pro",
        })

        assert res.status_code == 400
        assert asked == ["gemini-3-pro"], "no silent substitution behind your back"

    def test_the_network_dying_mid_sweep_is_reported_as_the_network(
        self, client, monkeypatch
    ):
        """Losing the connection while trying fallbacks blames the connection."""
        from iris.app.llm.base import LLMProviderError

        seen = []

        class Fake:
            def __init__(self, model):
                self.model = model

            async def generate(self, *a, **k):
                seen.append(self.model)
                if len(seen) == 1:
                    raise LLMProviderError("gemini: HTTP 404 — model not found")
                raise LLMProviderError("gemini: connection failed (ConnectError)")

            async def close(self):
                return None

        monkeypatch.setattr(setup_mod, "build_provider",
                            lambda name, creds: Fake(creds.get("model")))
        detail = client.post("/api/v1/setup",
                             json={"provider": "gemini", "api_key": KEY}).json()["detail"]
        assert "reach" in detail.lower()
        assert "model" not in detail.lower()
        assert len(seen) == 2, "it stopped sweeping once the network went"

    def test_a_working_key_is_saved(self, client, env_file, monkeypatch):
        class Fake:
            async def generate(self, *a, **k):
                return type("R", (), {"content": "ok"})()

            async def close(self):
                return None

        monkeypatch.setattr(setup_mod, "build_provider", lambda name, creds: Fake())
        body = client.post("/api/v1/setup",
                           json={"provider": "gemini", "api_key": KEY}).json()
        assert body["verified"] is True and body["persisted"] is True
        assert f"GEMINI_API_KEY={KEY}" in env_file.read_text()


class TestWhenTheFileCannotBeWritten:
    def test_it_still_works_now_and_says_it_will_not_survive(self, client, monkeypatch):
        """Claiming success and losing the key at the next restart is the worst
        of the available outcomes."""
        def explode(*a, **k):
            raise envfile.EnvWriteError("Could not write /etc/.env: Permission denied")

        monkeypatch.setattr(envfile, "update_env", explode)
        body = client.post(
            "/api/v1/setup",
            json={"provider": "gemini", "api_key": KEY, "verify": False},
        ).json()
        assert body["ok"] is True and body["persisted"] is False
        assert "forgotten" in body["warning"]
        assert settings.GEMINI_API_KEY == KEY          # applied to this process
