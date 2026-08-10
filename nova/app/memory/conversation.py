"""Conversation memory for tracking chat dialogue history."""

from typing import Any, Optional, Dict, List
from nova.app.memory.base import BaseMemory


class ConversationMemory(BaseMemory):
    """Memory module storing conversation dialogue history per conversation_id."""

    def __init__(self):
        # Maps conversation_id -> List of message dicts
        self._conversations: Dict[str, List[Dict[str, Any]]] = {}

    async def remember(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Key is conversation_id, value is message payload (role, content, etc.)."""
        if key not in self._conversations:
            self._conversations[key] = []
        if isinstance(value, list):
            self._conversations[key].extend(value)
        else:
            self._conversations[key].append(value)

    async def retrieve(self, key: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve conversation history for given conversation_id."""
        return self._conversations.get(key, [])

    async def forget(self, key: str) -> bool:
        if key in self._conversations:
            del self._conversations[key]
            return True
        return False

    async def clear(self) -> None:
        self._conversations.clear()
