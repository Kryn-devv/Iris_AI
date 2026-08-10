"""Structured logging for NOVA agent kernel."""

import logging
import sys
import contextvars
from typing import Optional, Dict, Any

# Context variables for request tracking
correlation_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)
task_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("task_id", default=None)


class ContextFilter(logging.Filter):
    """Inject correlation_id and task_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_ctx.get() or "N/A"
        record.task_id = task_id_ctx.get() or "N/A"
        return True


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure system-wide structured logging."""
    logger = logging.getLogger("nova")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [cid:%(correlation_id)s] [tid:%(task_id)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        logger.addHandler(handler)

    return logger


logger = setup_logging()


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with context filtering."""
    child_logger = logging.getLogger(f"nova.{name}")
    if not child_logger.handlers:
        for handler in logger.handlers:
            child_logger.addHandler(handler)
        child_logger.setLevel(logger.level)
    return child_logger
