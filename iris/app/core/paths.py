"""Cross-platform application path resolution for IRIS.

Every writable artifact IRIS produces (database, logs, cache, generated
documents, voice models, routine definitions) resolves through this module so
the same code works identically on Windows, Linux and macOS without hardcoded
paths.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

APP_DIRNAME = "IrisAI"


def _env_path(name: str) -> Path | None:
    """Return a Path from an environment variable if it is set and non-empty."""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


@lru_cache(maxsize=1)
def home_dir() -> Path:
    """User home directory."""
    return Path.home()


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """Per-user writable data directory for IRIS.

    Honors ``IRIS_DATA_DIR`` first so portable installs and tests can redirect
    all state to a temporary location.
    """
    override = _env_path("IRIS_DATA_DIR")
    if override:
        return override

    if sys.platform == "win32":
        base = _env_path("APPDATA") or (home_dir() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home_dir() / "Library" / "Application Support"
    else:
        base = _env_path("XDG_DATA_HOME") or (home_dir() / ".local" / "share")
    return base / APP_DIRNAME


@lru_cache(maxsize=1)
def config_dir() -> Path:
    """Per-user configuration directory."""
    override = _env_path("IRIS_CONFIG_DIR")
    if override:
        return override

    if sys.platform == "win32":
        return data_dir()
    if sys.platform == "darwin":
        return home_dir() / "Library" / "Preferences" / APP_DIRNAME
    base = _env_path("XDG_CONFIG_HOME") or (home_dir() / ".config")
    return base / APP_DIRNAME


@lru_cache(maxsize=1)
def cache_dir() -> Path:
    """Per-user cache directory (safe to delete at any time)."""
    override = _env_path("IRIS_CACHE_DIR")
    if override:
        return override

    if sys.platform == "win32":
        base = _env_path("LOCALAPPDATA") or (home_dir() / "AppData" / "Local")
        return base / APP_DIRNAME / "Cache"
    if sys.platform == "darwin":
        return home_dir() / "Library" / "Caches" / APP_DIRNAME
    base = _env_path("XDG_CACHE_HOME") or (home_dir() / ".cache")
    return base / APP_DIRNAME


def logs_dir() -> Path:
    """Directory holding rotating log files."""
    return data_dir() / "logs"


def workspace_dir() -> Path:
    """Default output directory for documents, decks and code IRIS generates."""
    override = _env_path("IRIS_WORKSPACE_DIR")
    if override:
        return override
    return home_dir() / "Iris"


def outputs_dir() -> Path:
    """Generated deliverables (decks, docs, sheets, PDFs)."""
    return workspace_dir() / "outputs"


def projects_dir() -> Path:
    """Scaffolded code projects."""
    return workspace_dir() / "projects"


def screenshots_dir() -> Path:
    """Captured screenshots."""
    return workspace_dir() / "screenshots"


def recordings_dir() -> Path:
    """Captured or synthesized audio."""
    return cache_dir() / "audio"


def models_dir() -> Path:
    """Downloaded offline voice / embedding model weights."""
    return data_dir() / "models"


def package_root() -> Path:
    """Filesystem root of the installed ``iris`` package."""
    return Path(__file__).resolve().parent.parent.parent


def static_dir() -> Path:
    """Bundled web UI assets."""
    return package_root() / "iris" / "app" / "static" if (package_root() / "iris").exists() \
        else Path(__file__).resolve().parent.parent / "static"


def ensure_dirs() -> dict[str, Path]:
    """Create every writable directory IRIS needs and return the resolved map."""
    resolved = {
        "data": data_dir(),
        "config": config_dir(),
        "cache": cache_dir(),
        "logs": logs_dir(),
        "workspace": workspace_dir(),
        "outputs": outputs_dir(),
        "projects": projects_dir(),
        "screenshots": screenshots_dir(),
        "recordings": recordings_dir(),
        "models": models_dir(),
    }
    for path in resolved.values():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            # A read-only or sandboxed location must never abort startup.
            continue
    return resolved


def default_database_url() -> str:
    """SQLite URL living inside the per-user data directory."""
    db_path = data_dir() / "iris.db"
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def reset_cache() -> None:
    """Clear memoized paths (used by tests that patch environment variables)."""
    for fn in (home_dir, data_dir, config_dir, cache_dir):
        fn.cache_clear()
