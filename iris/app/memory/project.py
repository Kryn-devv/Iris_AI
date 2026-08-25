from typing import Any, Optional, Dict, List
from datetime import datetime
from iris.app.memory.base import BaseMemory
from iris.app.schemas.memory import MemoryRecord, MemoryType, ConfidenceLevel
from iris.app.memory.sanitizer import MemorySanitizer


class ProjectMemory(BaseMemory):
    """Memory module storing workspace settings and project-scoped context."""

    def __init__(self):
        # Maps key -> MemoryRecord
        self._project_data: Dict[str, MemoryRecord] = {}
        # Default project info
        self._default_metadata = {
            "name": "IRIS Project",
            "version": "0.4.0",
            "phase": "Phase 4 - Advanced Memory & Personal Context",
            "offline_mode": True,
        }

    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata or {}
        sanitized_key = MemorySanitizer.sanitize_text(key)
        sanitized_val = MemorySanitizer.sanitize_value(value)
        project_id = meta.get("project_id", "default")

        record = MemoryRecord(
            type=MemoryType.PROJECT,
            key=sanitized_key,
            value=sanitized_val,
            content=meta.get("content") or f"Project '{project_id}' [{sanitized_key}]: {sanitized_val}",
            source=meta.get("source", "user"),
            importance=meta.get("importance", 0.8),
            confidence=meta.get("confidence", ConfidenceLevel.HIGH),
            project_id=project_id,
            conversation_id=meta.get("conversation_id"),
            tags=meta.get("tags", []),
            metadata=meta,
        )
        composite_key = f"{project_id}:{sanitized_key}"
        self._project_data[composite_key] = record

    async def retrieve(self, key: str) -> Optional[Any]:
        sanitized_key = MemorySanitizer.sanitize_text(key)
        # Try direct key or composite keys
        for comp_k, rec in self._project_data.items():
            if (comp_k == sanitized_key or rec.key == sanitized_key) and not rec.is_superseded:
                rec.last_accessed_at = datetime.now()
                return rec.value

        if key in self._default_metadata:
            return self._default_metadata[key]

        return None

    async def get_project_records(self, project_id: str) -> List[MemoryRecord]:
        """Retrieve all active memory records for a given project_id."""
        return [
            rec for rec in self._project_data.values()
            if rec.project_id == project_id and not rec.is_superseded
        ]

    async def forget(self, key: str) -> bool:
        sanitized_key = MemorySanitizer.sanitize_text(key)
        deleted = False
        keys_to_del = []
        for comp_k, rec in self._project_data.items():
            if comp_k == sanitized_key or rec.key == sanitized_key:
                rec.is_superseded = True
                keys_to_del.append(comp_k)
                deleted = True
        for k in keys_to_del:
            del self._project_data[k]
        return deleted

    async def clear(self) -> None:
        self._project_data.clear()

    def get_all(self) -> Dict[str, Any]:
        res = dict(self._default_metadata)
        for comp_k, rec in self._project_data.items():
            if not rec.is_superseded:
                res[rec.key] = rec.value
        return res
