"""Agent event and observability system for tracking safe execution states."""

from enum import Enum
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from pydantic import BaseModel, Field
from nova.app.core.logging import get_logger

logger = get_logger("agent.events")


class AgentEventType(str, Enum):
    """Safe execution states exposed by the agent event system (No chain-of-thought)."""
    AGENT_STARTED = "AGENT_STARTED"
    PLAN_CREATED = "PLAN_CREATED"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    PERMISSION_CHECK = "PERMISSION_CHECK"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    REPLAN_STARTED = "REPLAN_STARTED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    AGENT_CANCELLED = "AGENT_CANCELLED"
    MEMORY_CREATED = "MEMORY_CREATED"
    MEMORY_UPDATED = "MEMORY_UPDATED"
    MEMORY_RETRIEVED = "MEMORY_RETRIEVED"
    MEMORY_FORGOTTEN = "MEMORY_FORGOTTEN"
    MEMORY_CONFLICT_RESOLVED = "MEMORY_CONFLICT_RESOLVED"


class AgentEvent(BaseModel):
    """Structured agent lifecycle event."""
    event_id: str
    task_id: str
    correlation_id: str
    event_type: AgentEventType
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_name: Optional[str] = None
    duration_ms: Optional[float] = None
    success: Optional[bool] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class EventDispatcher:
    """Manages publishing and listening to agent execution events."""

    def __init__(self):
        self._listeners: List[Callable[[AgentEvent], None]] = []

    def register_listener(self, listener: Callable[[AgentEvent], None]) -> None:
        """Register callback listener for agent events."""
        self._listeners.append(listener)

    def dispatch(self, event: AgentEvent) -> None:
        """Publish event to all registered listeners and structured logs."""
        logger.info(
            f"Agent Event [{event.event_type.value}] for task '{event.task_id}' "
            f"(tool={event.tool_name or 'N/A'}, success={event.success})"
        )
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Error in event listener: {e}")


default_event_dispatcher = EventDispatcher()
