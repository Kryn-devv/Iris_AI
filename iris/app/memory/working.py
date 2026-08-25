"""Working memory for transient active task execution context."""

from typing import Any, Optional, Dict
from iris.app.memory.base import BaseMemory


class WorkingMemory(BaseMemory):
    """In-memory transient storage for active tasks."""

    def __init__(self):
        self._data: Dict[str, Any] = {}

    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        self._data[key] = value

    async def retrieve(self, key: str) -> Optional[Any]:
        return self._data.get(key)

    async def forget(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False

    async def clear(self) -> None:
        self._data.clear()

    def dump(self) -> Dict[str, Any]:
        """Return shallow copy of working memory contents."""
        return dict(self._data)
