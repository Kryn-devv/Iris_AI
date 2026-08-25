"""Structured logging for the IRIS agent kernel.

Supports human-readable console output (default), machine-readable JSON lines,
and a rotating file sink under the per-user data directory. Correlation and
task identifiers flow through :mod:`contextvars` so every line emitted while
handling a request is attributable.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
from typing import Any, Optional

from iris.app.core import paths

# Context variables for request tracking
correlation_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)
task_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("task_id", default=None)
channel_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("channel", default=None)

ROOT_LOGGER_NAME = "iris"

#: Substrings that mark a log field as secret; values are redacted on output.
_SECRET_HINTS = ("api_key", "apikey", "token", "secret", "password", "authorization", "bearer")


class ContextFilter(logging.Filter):
    """Inject correlation_id, task_id and channel into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_ctx.get() or "-"
        record.task_id = task_id_ctx.get() or "-"
        record.channel = channel_ctx.get() or "-"
        return True


class RedactingFilter(logging.Filter):
    """Best-effort redaction of credential-looking values in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed format args
            return True

        lowered = message.lower()
        if not any(hint in lowered for hint in _SECRET_HINTS):
            return True

        redacted = message
        for sep in ("=", ": ", ":"):
            for hint in _SECRET_HINTS:
                marker = f"{hint}{sep}"
                idx = redacted.lower().find(marker)
                while idx != -1:
                    start = idx + len(marker)
                    end = start
                    while end < len(redacted) and redacted[end] not in " \t\n,;)}'\"":
                        end += 1
                    if end - start > 4:
                        redacted = redacted[:start] + "[REDACTED]" + redacted[end:]
                        idx = redacted.lower().find(marker, start + 10)
                    else:
                        idx = redacted.lower().find(marker, end)
        record.msg = redacted
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "task_id": getattr(record, "task_id", "-"),
            "channel": getattr(record, "channel", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


CONSOLE_FORMAT = "[%(asctime)s] %(levelname)-7s [%(channel)s] [cid:%(correlation_id)s] [tid:%(task_id)s] %(name)s — %(message)s"


def _build_handlers(log_json: bool, log_to_file: bool) -> list[logging.Handler]:
    handlers: list[logging.Handler] = []

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        JsonFormatter() if log_json else logging.Formatter(CONSOLE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    )
    handlers.append(console)

    if log_to_file:
        try:
            log_dir = paths.logs_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "iris.log",
                maxBytes=5_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(JsonFormatter())
            handlers.append(file_handler)
        except OSError:
            # A read-only home directory must not prevent IRIS from starting.
            pass

    for handler in handlers:
        handler.addFilter(ContextFilter())
        handler.addFilter(RedactingFilter())
    return handlers


def setup_logging(
    log_level: str = "INFO",
    log_json: bool = False,
    log_to_file: bool = False,
) -> logging.Logger:
    """Configure system-wide structured logging (idempotent)."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(getattr(logging, str(log_level).upper(), logging.INFO))
    root.propagate = False

    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover
            pass

    for handler in _build_handlers(log_json=log_json, log_to_file=log_to_file):
        root.addHandler(handler)

    return root


logger = setup_logging()


def configure_from_settings() -> logging.Logger:
    """Reconfigure logging from the loaded settings (called during startup)."""
    from iris.app.core.config import settings  # imported late to avoid a cycle

    global logger
    logger = setup_logging(
        log_level=settings.LOG_LEVEL,
        log_json=settings.LOG_JSON,
        log_to_file=settings.LOG_TO_FILE,
    )
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a namespaced child logger that inherits the root handlers."""
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
