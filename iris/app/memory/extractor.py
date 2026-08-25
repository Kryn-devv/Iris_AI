"""Memory Extractor and Natural Language Command Parser."""

import re
from typing import Dict, Any, Optional, Tuple
from iris.app.schemas.memory import MemoryType, ConfidenceLevel
from iris.app.memory.sanitizer import MemorySanitizer
from iris.app.core.logging import get_logger

logger = get_logger("memory.extractor")


class MemoryExtractor:
    """Parses user input for explicit memory commands and selective durable information."""

    # Explicit command patterns
    REMEMBER_CMD = re.compile(r"^(?:please\s+)?remember\s+(?:that\s+)?(.+)", re.IGNORECASE)
    FORGET_CMD = re.compile(r"^(?:please\s+)?forget\s+(?:the\s+)?(.+)", re.IGNORECASE)
    RECALL_CMD = re.compile(r"^(?:what\s+do\s+you\s+remember\s+about|what(?:'s|\s+is|\s+microcontroller\s+does)?\s+(?:my|the)?)\s*(.+)", re.IGNORECASE)

    # Key patterns for facts and project attributes
    BUDGET_PATTERN = re.compile(r"(?:budget|cost)\s*(?:is|of|=)?\s*(?:₹|rs\.?|inr|\$)?\s*([\d,]+)", re.IGNORECASE)
    CONTROLLER_PATTERN = re.compile(r"(?:microcontroller|controller|board|processor|chip)\s*(?:is|uses|=)?\s*([a-zA-Z0-9_\-\s]+)", re.IGNORECASE)

    @classmethod
    def parse_command(cls, text: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Determine if text contains an explicit memory command (remember, forget, recall)."""
        clean = text.strip()

        # 1. Explicit Forget Command
        forget_match = cls.FORGET_CMD.match(clean)
        if forget_match:
            target = forget_match.group(1).strip().rstrip(".")
            # Standardize target key
            key = cls._normalize_key(target)
            return "forget", {"key": key, "raw_target": target}

        # 2. Explicit Remember Command
        rem_match = cls.REMEMBER_CMD.match(clean)
        if rem_match:
            fact = rem_match.group(1).strip().rstrip(".")
            extracted = cls.extract_fact_from_text(fact)
            return "remember", extracted

        # 3. Explicit Recall Command
        lower = clean.lower()
        if "remember about" in lower or "recall" in lower or "tell me about my" in lower or "what is my" in lower or "what's my" in lower or "what microcontroller" in lower:
            # Filter memory attribute targets vs tool queries (calculator, system_info, time)
            if any(term in lower for term in ["budget", "microcontroller", "controller", "board", "preference", "favorite", "hobby", "skill"]):
                key = cls._normalize_key(clean)
                return "recall", {"key": key, "query": clean}

        return None, None

    @classmethod
    def extract_fact_from_text(cls, text: str) -> Dict[str, Any]:
        """Extract key-value pairs and metadata from fact text."""
        clean = MemorySanitizer.sanitize_text(text.strip())
        lower = clean.lower()

        key = "general_fact"
        value = clean
        mem_type = MemoryType.SEMANTIC
        importance = 0.7

        # Check Budget
        b_match = cls.BUDGET_PATTERN.search(lower)
        if b_match or "budget" in lower:
            key = "robot_budget" if "robot" in lower else "budget"
            amount = b_match.group(1) if b_match else clean
            value = f"₹{amount}" if b_match else clean
            mem_type = MemoryType.PROJECT
            importance = 0.9

        # Check Microcontroller / Hardware
        c_match = cls.CONTROLLER_PATTERN.search(lower)
        if c_match or "esp32" in lower or "microcontroller" in lower:
            key = "robot_microcontroller" if "robot" in lower else "microcontroller"
            value = "ESP32" if "esp32" in lower else (c_match.group(1).strip() if c_match else clean)
            mem_type = MemoryType.PROJECT
            importance = 0.9

        # Check Preference
        if "prefer" in lower or "like" in lower:
            key = "user_preference"
            mem_type = MemoryType.SEMANTIC
            importance = 0.8

        return {
            "key": key,
            "value": value,
            "content": clean,
            "type": mem_type,
            "importance": importance,
            "confidence": ConfidenceLevel.HIGH,
        }

    @staticmethod
    def _normalize_key(target: str) -> str:
        t = target.lower()
        if "budget" in t:
            return "robot_budget" if "robot" in t else "budget"
        if "microcontroller" in t or "controller" in t or "board" in t or "esp32" in t:
            return "robot_microcontroller" if "robot" in t else "microcontroller"
        return t.replace(" ", "_")
