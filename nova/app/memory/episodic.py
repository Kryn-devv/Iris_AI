"""Episodic memory store for discrete events and activity history."""

from typing import Any, Optional, Dict, List
from datetime import datetime
from nova.app.memory.base import BaseMemory
from nova.app.schemas.memory import MemoryRecord, MemoryType, ConfidenceLevel
from nova.app.memory.sanitizer import MemorySanitizer


class EpisodicMemory(BaseMemory):
    """Memory store tracking discrete user/agent events over time."""

    def __init__(self):
        self._events: List[MemoryRecord] = []

    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata or {}
        sanitized_val = MemorySanitizer.sanitize_value(value)
        content_str = str(sanitized_val)
        
        record = MemoryRecord(
            type=MemoryType.EPISODIC,
            key=MemorySanitizer.sanitize_text(key),
            value=sanitized_val,
            content=content_str,
            source=meta.get("source", "user"),
            importance=meta.get("importance", 0.5),
            confidence=ConfidenceLevel.HIGH,
            project_id=meta.get("project_id"),
            conversation_id=meta.get("conversation_id"),
            tags=meta.get("tags", []),
            metadata=meta,
        )
        self._events.append(record)

    async def retrieve(self, key: str) -> Optional[Any]:
        for rec in reversed(self._events):
            if rec.key == key and not rec.is_superseded:
                rec.last_accessed_at = datetime.now()
                return rec.value
        return None

    async def get_events(self, limit: int = 10, project_id: Optional[str] = None) -> List[MemoryRecord]:
        """Retrieve recent episodic events."""
        filtered = [
            e for e in reversed(self._events)
            if not e.is_superseded and (project_id is None or e.project_id == project_id)
        ]
        return filtered[:limit]

    async def forget(self, key: str) -> bool:
        found = False
        for rec in self._events:
            if rec.key == key:
                rec.is_superseded = True
                found = True
        return found

    async def clear(self) -> None:
        self._events.clear()
