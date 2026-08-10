"""Long-term persistent memory backed by SQLite."""

import json
from typing import Any, Optional, Dict
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from nova.app.memory.base import BaseMemory
from nova.app.database.models import MemoryRecordModel
from nova.app.core.logging import get_logger

logger = get_logger("memory.long_term")


class LongTermMemory(BaseMemory):
    """SQLite-backed long term memory for persistent facts and state."""

    def __init__(self, session: Optional[AsyncSession] = None):
        self.session = session
        self._cache: Dict[str, Any] = {}

    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._cache[key] = value
        if self.session:
            try:
                stmt = select(MemoryRecordModel).where(
                    MemoryRecordModel.memory_type == "long_term",
                    MemoryRecordModel.key == key
                )
                res = await self.session.execute(stmt)
                record = res.scalars().first()

                val_str = json.dumps(value)
                if record:
                    record.value_json = val_str
                else:
                    record = MemoryRecordModel(
                        memory_type="long_term",
                        key=key,
                        value_json=val_str,
                    )
                    self.session.add(record)
                await self.session.commit()
            except Exception as e:
                logger.error(f"Failed to persist to long_term memory DB: {e}")
                await self.session.rollback()

    async def retrieve(self, key: str) -> Optional[Any]:
        if key in self._cache:
            return self._cache[key]

        if self.session:
            try:
                stmt = select(MemoryRecordModel).where(
                    MemoryRecordModel.memory_type == "long_term",
                    MemoryRecordModel.key == key
                )
                res = await self.session.execute(stmt)
                record = res.scalars().first()
                if record:
                    val = json.loads(record.value_json)
                    self._cache[key] = val
                    return val
            except Exception as e:
                logger.error(f"Failed to retrieve from long_term memory DB: {e}")

        return None

    async def forget(self, key: str) -> bool:
        existed = key in self._cache
        self._cache.pop(key, None)

        if self.session:
            try:
                stmt = select(MemoryRecordModel).where(
                    MemoryRecordModel.memory_type == "long_term",
                    MemoryRecordModel.key == key
                )
                res = await self.session.execute(stmt)
                record = res.scalars().first()
                if record:
                    await self.session.delete(record)
                    await self.session.commit()
                    return True
            except Exception as e:
                logger.error(f"Failed to delete key '{key}' from long_term DB: {e}")
                await self.session.rollback()

        return existed

    async def clear(self) -> None:
        self._cache.clear()
        if self.session:
            try:
                stmt = select(MemoryRecordModel).where(MemoryRecordModel.memory_type == "long_term")
                res = await self.session.execute(stmt)
                for record in res.scalars():
                    await self.session.delete(record)
                await self.session.commit()
            except Exception as e:
                logger.error(f"Failed to clear long_term memory DB: {e}")
                await self.session.rollback()
