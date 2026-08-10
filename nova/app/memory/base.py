"""Abstract Base Class for Memory components in NOVA."""

from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, List


class BaseMemory(ABC):
    """Abstract interface for all NOVA memory modules."""

    @abstractmethod
    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Store an item in memory."""
        pass

    @abstractmethod
    async def retrieve(self, key: str) -> Optional[Any]:
        """Retrieve an item by key from memory."""
        pass

    @abstractmethod
    async def forget(self, key: str) -> bool:
        """Delete an item from memory."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all stored items in this memory module."""
        pass
