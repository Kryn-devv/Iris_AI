"""Access control for remote (phone / LAN) usage.

IRIS binds to localhost by default and needs no authentication. The moment the
user enables LAN access or a tunnel so their phone can reach it, a bearer token
becomes mandatory: :func:`ensure_token` mints one on first use and persists it
to the config directory with owner-only permissions.
"""

from __future__ import annotations

import hmac
import os
import secrets
import stat
import time
from collections import defaultdict, deque
from typing import Deque, Optional

from iris.app.core import paths
from iris.app.core.config import settings
from iris.app.core.logging import get_logger

logger = get_logger("core.auth")

TOKEN_FILENAME = "api_token"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient", "unix"})


def token_path() -> "os.PathLike[str]":
    return paths.config_dir() / TOKEN_FILENAME


def ensure_token() -> str:
    """Return the API token, generating and persisting one if needed."""
    if settings.API_TOKEN:
        return settings.API_TOKEN

    path = paths.config_dir() / TOKEN_FILENAME
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                settings.API_TOKEN = existing
                return existing
    except OSError as exc:
        logger.warning("Could not read stored API token: %s", exc)

    token = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.warning("Could not persist API token (%s); using an in-memory token.", exc)

    settings.API_TOKEN = token
    return token


def rotate_token() -> str:
    """Invalidate the current token and mint a fresh one."""
    path = paths.config_dir() / TOKEN_FILENAME
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
    settings.API_TOKEN = None
    return ensure_token()


def auth_required() -> bool:
    """True when incoming requests must present a bearer token."""
    return bool(settings.REQUIRE_AUTH or settings.ALLOW_LAN_ACCESS)


def is_loopback(client_host: Optional[str]) -> bool:
    """True when the request originates from this machine."""
    return (client_host or "").lower() in _LOOPBACK_HOSTS


def verify_token(presented: Optional[str]) -> bool:
    """Constant-time comparison of a presented token against the real one."""
    if not presented:
        return False
    return hmac.compare_digest(presented.strip(), ensure_token())


def extract_token(authorization: Optional[str], header_token: Optional[str], query_token: Optional[str]) -> Optional[str]:
    """Pull a token out of the Authorization header, X-Iris-Token, or ?token=."""
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
            return parts[1].strip()
        return authorization.strip()
    return header_token or query_token


class RateLimiter:
    """Simple fixed-window-per-client rate limiter."""

    def __init__(self, limit_per_minute: Optional[int] = None):
        self.limit = limit_per_minute if limit_per_minute is not None else settings.RATE_LIMIT_PER_MINUTE
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def check(self, client_key: str) -> bool:
        """Record a hit and return whether the client is still under the limit."""
        if self.limit <= 0:
            return True
        now = time.monotonic()
        window = self._hits[client_key]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True

    def reset(self, client_key: Optional[str] = None) -> None:
        if client_key is None:
            self._hits.clear()
        else:
            self._hits.pop(client_key, None)


default_rate_limiter = RateLimiter()
