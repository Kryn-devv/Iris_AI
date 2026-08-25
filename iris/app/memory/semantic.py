"""Semantic memory store for durable facts, preferences, and knowledge records."""

from typing import Any, Optional, Dict, List
from datetime import datetime
from iris.app.memory.base import BaseMemory
from iris.app.schemas.memory import MemoryRecord, MemoryType, ConfidenceLevel
from iris.app.memory.sanitizer import MemorySanitizer


class SemanticMemory(BaseMemory):
    """Memory store tracking facts, user preferences, and domain knowledge."""

    def __init__(self):
        # Maps key -> MemoryRecord
        self._facts: Dict[str, MemoryRecord] = {}

    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata or {}
        sanitized_key = MemorySanitizer.sanitize_text(key)
        sanitized_val = MemorySanitizer.sanitize_value(value)
        content_str = meta.get("content") or f"{sanitized_key}: {sanitized_val}"

        # Conflict resolution: update existing record if key exists
        if sanitized_key in self._facts:
            existing = self._facts[sanitized_key]
            existing.value = sanitized_val
            existing.content = content_str
            existing.updated_at = datetime.now()
            existing.importance = meta.get("importance", existing.importance)
            existing.confidence = meta.get("confidence", existing.confidence)
            existing.metadata.update(meta)
        else:
            record = MemoryRecord(
                type=MemoryType.SEMANTIC,
                key=sanitized_key,
                value=sanitized_val,
                content=content_str,
                source=meta.get("source", "user"),
                importance=meta.get("importance", 0.7),
                confidence=meta.get("confidence", ConfidenceLevel.HIGH),
                project_id=meta.get("project_id"),
                conversation_id=meta.get("conversation_id"),
                tags=meta.get("tags", []),
                metadata=meta,
            )
            self._facts[sanitized_key] = record

    async def retrieve(self, key: str) -> Optional[Any]:
        sanitized_key = MemorySanitizer.sanitize_text(key)
        if sanitized_key in self._facts:
            rec = self._facts[sanitized_key]
            if not rec.is_superseded:
                rec.last_accessed_at = datetime.now()
                return rec.value
        return None

    async def get_record(self, key: str) -> Optional[MemoryRecord]:
        """Retrieve full MemoryRecord by key."""
        sanitized_key = MemorySanitizer.sanitize_text(key)
        rec = self._facts.get(sanitized_key)
        if rec and not rec.is_superseded:
            rec.last_accessed_at = datetime.now()
            return rec
        return None

    async def list_records(self) -> List[MemoryRecord]:
        """List all active semantic memory records."""
        return [r for r in self._facts.values() if not r.is_superseded]

    async def forget(self, key: str) -> bool:
        sanitized_key = MemorySanitizer.sanitize_text(key)
        if sanitized_key in self._facts:
            self._facts[sanitized_key].is_superseded = True
            del self._facts[sanitized_key]
            return True
        return False

    async def clear(self) -> None:
        self._facts.clear()
