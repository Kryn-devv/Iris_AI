"""Regression tests for absolute ``.env`` resolution.

IRIS is launched at login by a Windows registry Run key / macOS LaunchAgent /
XDG autostart entry, none of which set a working directory. A relative
``env_file=".env"`` therefore resolved against ``C:\\Windows\\System32`` (or
similar) and silently ignored the user's API keys on every boot — commands kept
working, so nothing looked broken, but conversation quietly ran offline.

These tests pin the fix: candidates are absolute, include the project root, and
survive being loaded from an unrelated working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from iris.app.core import paths
from iris.app.core.config import Settings


def test_env_candidates_are_absolute() -> None:
    for candidate in paths.env_file_candidates():
        assert candidate.is_absolute(), f"{candidate} must be absolute"


def test_project_root_env_is_a_candidate() -> None:
    """The ``.env`` sitting next to the ``iris`` package must always be read."""
    assert (paths.project_root() / ".env").resolve() in paths.env_file_candidates()


def test_project_root_contains_the_package() -> None:
    assert (paths.project_root() / "iris").is_dir()


def test_env_candidates_are_deduplicated() -> None:
    candidates = paths.env_file_candidates()
    assert len(candidates) == len(set(candidates))


def test_settings_read_env_from_foreign_working_directory(tmp_path: Path, monkeypatch) -> None:
    """Keys are picked up even when the process starts outside the project."""
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=sk-or-v1-regression\n", encoding="utf-8")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    # Simulate the login-launch case: cwd has no .env, but an absolute path does.
    loaded = Settings(_env_file=(str(env_file),))

    assert loaded.OPENROUTER_API_KEY == "sk-or-v1-regression"
    assert "openrouter" in loaded.configured_providers()


def test_relative_env_file_would_have_missed_it(tmp_path: Path, monkeypatch) -> None:
    """Documents the original bug: a relative path finds nothing from elsewhere."""
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-regression\n", encoding="utf-8")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    missed = Settings(_env_file=(".env",))
    assert missed.OPENROUTER_API_KEY is None
    assert missed.configured_providers() == []


def test_existing_env_files_only_returns_real_files() -> None:
    for path in paths.existing_env_files():
        assert path.is_file()


def test_env_var_still_overrides_env_file(tmp_path: Path, monkeypatch) -> None:
    """A real environment variable must win over the file (used by the CLI flags)."""
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=1234\n", encoding="utf-8")
    monkeypatch.setenv("PORT", "4321")

    loaded = Settings(_env_file=(str(env_file),))
    assert loaded.PORT == 4321
