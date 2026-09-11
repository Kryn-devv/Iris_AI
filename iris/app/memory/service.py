"""Unified MemoryService coordinating layered memory stores, search, and relevance scoring."""

import math
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from iris.app.memory.working import WorkingMemory
from iris.app.memory.conversation import ConversationMemory
from iris.app.memory.episodic import EpisodicMemory
from iris.app.memory.semantic import SemanticMemory
from iris.app.memory.project import ProjectMemory
from iris.app.memory.long_term import LongTermMemory
from iris.app.database.database import AsyncSessionLocal
from iris.app.schemas.memory import MemoryRecord, MemoryType, ConfidenceLevel
from iris.app.memory.sanitizer import MemorySanitizer
from iris.app.core.logging import get_logger

logger = get_logger("memory.service")


class MemoryRelevanceScorer:
    """Calculates deterministic relevance scores for memory records."""

    @staticmethod
    def calculate_score(
        query: str,
        record: MemoryRecord,
        target_project_id: Optional[str] = None,
    ) -> float:
        """Calculate relevance score based on keyword match, importance, recency, and project matching."""
        if record.is_superseded:
            return 0.0

        query_terms = [t.lower() for t in query.split() if len(t) > 1]
        if not query_terms:
            return 0.0

        record_text = f"{record.key} {record.content} {' '.join(record.tags)}".lower()

        # 1. Keyword Match Score (0.0 to 1.0)
        matched_terms = sum(1 for term in query_terms if term in record_text)
        keyword_score = matched_terms / max(len(query_terms), 1)

        if keyword_score == 0.0:
            return 0.0

        # 2. Importance Score (0.0 to 1.0)
        importance_score = record.importance

        # 3. Recency Decay Score (Exponential decay over hours)
        hours_old = (datetime.now() - record.updated_at).total_seconds() / 3600.0
        recency_score = math.exp(-0.01 * hours_old)  # Slow decay

        # 4. Project Match Bonus (0.0 or 1.0)
        project_score = 1.0 if target_project_id and record.project_id == target_project_id else 0.0

        # Weighted combination
        final_score = (keyword_score * 0.4) + (importance_score * 0.3) + (recency_score * 0.2) + (project_score * 0.1)
        return round(final_score, 4)


class MemoryService:
    """Central Memory Service orchestrating Working, Conversation, Episodic, Semantic, and Project memories."""

    def __init__(self, long_term_memory: Optional[LongTermMemory] = None):
        self.working_memory = WorkingMemory()
        self.conversation_memory = ConversationMemory()
        self.episodic_memory = EpisodicMemory()
        self.semantic_memory = SemanticMemory()
        self.project_memory = ProjectMemory()
        # Durable facts go to SQLite as well, and are read back on the first
        # touch after boot — "remember my budget" has to outlive a restart.
        # Nothing reaches the database until main() has initialised it.
        self.long_term_memory = long_term_memory or LongTermMemory(session_factory=AsyncSessionLocal)
        self.relevance_scorer = MemoryRelevanceScorer()
        self._hydrated = False

    #: Layers whose facts are worth keeping across restarts. Working memory is
    #: scratch by definition and conversation history has its own store.
    _DURABLE = (MemoryType.SEMANTIC, MemoryType.PROJECT, MemoryType.EPISODIC)

    async def _ensure_hydrated(self) -> None:
        """Load what the previous run remembered into the in-memory layers."""
        if self._hydrated:
            return
        self._hydrated = True
        if not self.long_term_memory.persistent():
            return
        loaded = 0
        for key, value, meta in await self.long_term_memory.list_all():
            layer = str(meta.get("layer", MemoryType.SEMANTIC.value))
            store_meta = {k: v for k, v in meta.items() if k != "layer"}
            try:
                if layer == MemoryType.PROJECT.value:
                    await self.project_memory.remember(key, value, store_meta)
                elif layer == MemoryType.EPISODIC.value:
                    await self.episodic_memory.remember(key, value, store_meta)
                else:
                    await self.semantic_memory.remember(key, value, store_meta)
                loaded += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not block boot
                logger.warning("Skipped a stored memory '%s': %s", key, exc)
        if loaded:
            logger.info("Restored %d remembered facts from the database.", loaded)

    async def remember(
        self,
        key: str,
        value: Any,
        memory_type: MemoryType = MemoryType.SEMANTIC,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Sanitize and store a memory entry in the appropriate memory layer."""
        await self._ensure_hydrated()
        meta = metadata or {}
        sanitized_key = MemorySanitizer.sanitize_text(key)
        sanitized_val = MemorySanitizer.sanitize_value(value)

        if memory_type == MemoryType.WORKING:
            await self.working_memory.remember(sanitized_key, sanitized_val, meta)
        elif memory_type == MemoryType.EPISODIC:
            await self.episodic_memory.remember(sanitized_key, sanitized_val, meta)
        elif memory_type == MemoryType.PROJECT:
            await self.project_memory.remember(sanitized_key, sanitized_val, meta)
        else:  # SEMANTIC / DEFAULT
            memory_type = MemoryType.SEMANTIC
            await self.semantic_memory.remember(sanitized_key, sanitized_val, meta)

        if memory_type in self._DURABLE:
            durable_meta = {k: v for k, v in meta.items() if k != "type"}
            durable_meta["layer"] = memory_type.value
            await self.long_term_memory.remember(sanitized_key, sanitized_val, durable_meta)

        logger.info(f"Memory stored [{memory_type.value}]: key='{sanitized_key}'")

    async def retrieve(self, key: str, memory_type: Optional[MemoryType] = None) -> Optional[Any]:
        """Retrieve stored value by key."""
        await self._ensure_hydrated()
        sanitized_key = MemorySanitizer.sanitize_text(key)
        if memory_type == MemoryType.WORKING:
            return await self.working_memory.retrieve(sanitized_key)
        elif memory_type == MemoryType.PROJECT:
            return await self.project_memory.retrieve(sanitized_key)
        elif memory_type == MemoryType.EPISODIC:
            return await self.episodic_memory.retrieve(sanitized_key)
        elif memory_type == MemoryType.SEMANTIC:
            return await self.semantic_memory.retrieve(sanitized_key)

        # Fallback multi-layer lookup order
        for store in (self.semantic_memory, self.project_memory, self.episodic_memory, self.working_memory):
            val = await store.retrieve(sanitized_key)
            if val is not None:
                return val

        return await self.long_term_memory.retrieve(sanitized_key)

    async def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        project_id: Optional[str] = None,
        limit: int = 5,
        min_relevance: float = 0.1,
    ) -> List[Tuple[MemoryRecord, float]]:
        """Search memory entries across layers using relevance scoring."""
        await self._ensure_hydrated()
        records: List[MemoryRecord] = []

        if memory_type is None or memory_type == MemoryType.SEMANTIC:
            records.extend(await self.semantic_memory.list_records())

        if memory_type is None or memory_type == MemoryType.PROJECT:
            if project_id:
                records.extend(await self.project_memory.get_project_records(project_id))
            else:
                for comp_k, rec in self.project_memory._project_data.items():
                    if not rec.is_superseded:
                        records.append(rec)

        if memory_type is None or memory_type == MemoryType.EPISODIC:
            records.extend(await self.episodic_memory.get_events(limit=20, project_id=project_id))

        results: List[Tuple[MemoryRecord, float]] = []
        for rec in records:
            score = self.relevance_scorer.calculate_score(query, rec, target_project_id=project_id)
            if score >= min_relevance:
                results.append((rec, score))

        # Sort by relevance score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    async def forget(self, key: str, memory_type: Optional[MemoryType] = None) -> bool:
        """Forget/delete memory entry across layers."""
        await self._ensure_hydrated()
        sanitized_key = MemorySanitizer.sanitize_text(key)
        forgot_any = False

        if memory_type is None or memory_type in self._DURABLE:
            # Whatever layer held it, the durable copy goes too — otherwise the
            # forgotten fact comes back on the next restart.
            forgot_any |= await self.long_term_memory.forget(sanitized_key)

        if memory_type is None or memory_type == MemoryType.SEMANTIC:
            forgot_any |= await self.semantic_memory.forget(sanitized_key)

        if memory_type is None or memory_type == MemoryType.PROJECT:
            forgot_any |= await self.project_memory.forget(sanitized_key)

        if memory_type is None or memory_type == MemoryType.EPISODIC:
            forgot_any |= await self.episodic_memory.forget(sanitized_key)

        if memory_type is None or memory_type == MemoryType.WORKING:
            forgot_any |= await self.working_memory.forget(sanitized_key)

        logger.info(f"Memory forget executed for key='{sanitized_key}': success={forgot_any}")
        return forgot_any

    async def clear(self) -> None:
        """Clear all stored memories."""
        await self.working_memory.clear()
        await self.conversation_memory.clear()
        await self.episodic_memory.clear()
        await self.semantic_memory.clear()
        await self.project_memory.clear()
        await self.long_term_memory.clear()
