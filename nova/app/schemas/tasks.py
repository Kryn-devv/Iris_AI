"""Pydantic schemas for Tasks and Task status."""

from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Lifecycle statuses for NOVA tasks."""
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStepRecord(BaseModel):
    """Log record of a single planning or execution step within a task."""
    step_number: int
    step_type: str  # "plan", "tool_call", "observation", "evaluation"
    description: str
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class TaskCreate(BaseModel):
    """Payload to launch a task."""
    user_input: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    """Detailed view of a task."""
    task_id: str
    user_input: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    current_step: int = 0
    result: Optional[Any] = None
    error: Optional[str] = None
    steps: List[TaskStepRecord] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
