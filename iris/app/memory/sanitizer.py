"""Privacy and sanitization layer for redacting credentials before memory storage or logging."""

import re
from typing import Any, Dict

# Regex patterns for sensitive data
SENSITIVE_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"(password|passwd|secret|api_key|token)\s*[:=]\s*['\"]?([^\s'\";]+)['\"]?", re.IGNORECASE),
    re.compile(r"authorization\s*:\s*bearer\s+[^\s]+", re.IGNORECASE),
]


class MemorySanitizer:
    """Sanitizes text and metadata to prevent persisting or leaking secrets."""

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Redact sensitive credentials in string content."""
        if not text:
            return text

        sanitized = text
        for pattern in SENSITIVE_PATTERNS:
            # Handle key-value capture group pattern (e.g. password=12345)
            if pattern.groups == 2:
                sanitized = pattern.sub(r"\1=[REDACTED]", sanitized)
            else:
                sanitized = pattern.sub("[REDACTED]", sanitized)

        return sanitized

    @classmethod
    def sanitize_value(cls, value: Any) -> Any:
        """Recursively sanitize dicts, lists, or strings."""
        if isinstance(value, str):
            return cls.sanitize_text(value)
        elif isinstance(value, dict):
            clean_dict = {}
            for k, v in value.items():
                if any(sec in k.lower() for sec in ["password", "secret", "token", "api_key", "auth"]):
                    clean_dict[k] = "[REDACTED]"
                else:
                    clean_dict[k] = cls.sanitize_value(v)
            return clean_dict
        elif isinstance(value, list):
            return [cls.sanitize_value(item) for item in value]
        return value
