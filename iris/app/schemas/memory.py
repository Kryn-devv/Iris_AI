"""Pydantic schemas for IRIS Layered Memory system."""

from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid
from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """Memory layer classification."""
    WORKING = "working"
    CONVERSATION = "conversation"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROJECT = "project"


class ConfidenceLevel(str, Enum):
    """Confidence rating for extracted memory entries."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class MemoryRecord(BaseModel):
    """Structured memory record encapsulation."""
    id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    type: MemoryType = MemoryType.SEMANTIC
    key: str
    value: Any = None
    content: str = ""
    source: str = "user"  # "user", "agent", "system"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    last_accessed_at: datetime = Field(default_factory=datetime.now)
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_superseded: bool = False


class MemoryCreatePayload(BaseModel):
    """Payload to store or update a memory record."""
    type: Optional[MemoryType] = None
    memory_type: Optional[MemoryType] = None
    key: str
    value: Any = None
    content: Optional[str] = None
    importance: float = 0.5
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def get_memory_type(self) -> MemoryType:
        if self.memory_type:
            return self.memory_type
        if self.type:
            return self.type
        return MemoryType.SEMANTIC


class MemorySearchQuery(BaseModel):
    """Payload for memory search operations."""
    query: str
    type: Optional[MemoryType] = None
    project_id: Optional[str] = None
    limit: int = 5
    min_relevance: float = 0.1
