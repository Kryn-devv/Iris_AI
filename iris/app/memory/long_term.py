"""Long-term persistent memory backed by SQLite.

"Remember my budget is 5000" has to survive a restart, or it is not memory —
so every durable fact the MemoryService takes in is also written here, and
read back into the in-memory layers the first time memory is touched after
boot. The store works in two modes:

* ``session`` — one caller-managed session (what the tests use).
* ``session_factory`` — a new session per operation, opened only once the
  database has been initialised (``database.is_ready()``); before that, and
  with neither given, it is a plain in-process cache.
"""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from iris.app.memory.base import BaseMemory
from iris.app.database import database
from iris.app.database.models import MemoryRecordModel
from iris.app.core.logging import get_logger

logger = get_logger("memory.long_term")

#: The memory_type column value for rows this store owns. Which in-memory
#: layer a row belongs to lives in metadata_json["layer"].
ROW_TYPE = "long_term"


class LongTermMemory(BaseMemory):
    """SQLite-backed long term memory for persistent facts and state."""

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
    ):
        self.session = session
        self.session_factory = session_factory
        self._cache: Dict[str, Any] = {}

    def persistent(self) -> bool:
        """True when a write will actually reach the database right now."""
        return self.session is not None or (
            self.session_factory is not None and database.is_ready()
        )

    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._cache[key] = value
        if not self.persistent():
            return
        meta = dict(metadata or {})
        try:
            async with self._session() as s:
                record = await self._find(s, key)
                val_str = json.dumps(value, default=str)
                meta_str = json.dumps(meta, default=str)
                if record:
                    record.value_json = val_str
                    record.metadata_json = meta_str
                    if "importance" in meta:
                        record.importance = float(meta["importance"])
                else:
                    record = MemoryRecordModel(
                        memory_type=ROW_TYPE,
                        key=key,
                        value_json=val_str,
                        content=str(meta.get("content") or f"{key}: {value}"),
                        source=str(meta.get("source", "user")),
                        importance=float(meta.get("importance", 0.5)),
                        project_id=meta.get("project_id"),
                        conversation_id=meta.get("conversation_id"),
                        metadata_json=meta_str,
                    )
                    s.add(record)
                await s.commit()
        except Exception as e:  # noqa: BLE001 - memory must never take the turn down
            logger.error(f"Failed to persist to long_term memory DB: {e}")

    async def retrieve(self, key: str) -> Optional[Any]:
        if key in self._cache:
            return self._cache[key]
        if not self.persistent():
            return None
        try:
            async with self._session() as s:
                record = await self._find(s, key)
                if record:
                    val = json.loads(record.value_json)
                    self._cache[key] = val
                    return val
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to retrieve from long_term memory DB: {e}")
        return None

    async def list_all(self) -> List[Tuple[str, Any, Dict[str, Any]]]:
        """Every stored fact as (key, value, metadata) — what boot reads back."""
        if not self.persistent():
            return [(k, v, {}) for k, v in self._cache.items()]
        out: List[Tuple[str, Any, Dict[str, Any]]] = []
        try:
            async with self._session() as s:
                res = await s.execute(
                    select(MemoryRecordModel).where(MemoryRecordModel.memory_type == ROW_TYPE)
                )
                for record in res.scalars():
                    try:
                        value = json.loads(record.value_json)
                        meta = json.loads(record.metadata_json) if record.metadata_json else {}
                    except ValueError:
                        continue
                    self._cache[record.key] = value
                    out.append((record.key, value, meta if isinstance(meta, dict) else {}))
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to list long_term memory DB: {e}")
        return out

    async def forget(self, key: str) -> bool:
        existed = key in self._cache
        self._cache.pop(key, None)
        if not self.persistent():
            return existed
        try:
            async with self._session() as s:
                record = await self._find(s, key)
                if record:
                    await s.delete(record)
                    await s.commit()
                    return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to delete key '{key}' from long_term DB: {e}")
        return existed

    async def clear(self) -> None:
        self._cache.clear()
        if not self.persistent():
            return
        try:
            async with self._session() as s:
                res = await s.execute(
                    select(MemoryRecordModel).where(MemoryRecordModel.memory_type == ROW_TYPE)
                )
                for record in res.scalars():
                    await s.delete(record)
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to clear long_term memory DB: {e}")

    # ------------------------------------------------------------ helpers
    def _session(self):
        """An async context manager yielding the session to use.

        A caller-owned session is handed back untouched (and not closed);
        a factory-made one is closed after the operation.
        """
        if self.session is not None:
            return _Borrowed(self.session)
        return self.session_factory()

    @staticmethod
    async def _find(s: AsyncSession, key: str) -> Optional[MemoryRecordModel]:
        res = await s.execute(
            select(MemoryRecordModel).where(
                MemoryRecordModel.memory_type == ROW_TYPE,
                MemoryRecordModel.key == key,
            )
        )
        return res.scalars().first()


class _Borrowed:
    """Wraps a session someone else owns so it can be used with ``async with``."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            try:
                await self._session.rollback()
            except Exception:  # noqa: BLE001
                pass
        return False
