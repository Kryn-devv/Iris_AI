"""Task Manager for creating, tracking, and cancelling background tasks."""

import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from nova.app.schemas.tasks import TaskStatus, TaskResponse, TaskStepRecord
from nova.app.agent.state import AgentState
from nova.app.core.logging import get_logger

logger = get_logger("agent.task_manager")


class TaskManager:
    """Manages active tasks, state retention, and async cancellation."""

    def __init__(self):
        self._tasks: Dict[str, AgentState] = {}
        self._async_tasks: Dict[str, asyncio.Task] = {}

    def create_task(self, user_input: str, task_id: Optional[str] = None, correlation_id: Optional[str] = None) -> AgentState:
        """Initialize and register a new task."""
        state = AgentState(user_input=user_input, task_id=task_id, correlation_id=correlation_id)
        self._tasks[state.task_id] = state
        logger.info(f"Created task '{state.task_id}' with status PENDING")
        return state

    def get_task(self, task_id: str) -> Optional[AgentState]:
        """Retrieve active or completed task by task_id."""
        return self._tasks.get(task_id)

    def register_async_task(self, task_id: str, async_task: asyncio.Task) -> None:
        """Register running asyncio Task handle for cancellation support."""
        self._async_tasks[task_id] = async_task

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        state = self.get_task(task_id)
        if not state:
            return False

        if state.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            logger.info(f"Task '{task_id}' is already in terminal state '{state.status.value}'.")
            return False

        # Cancel asyncio handle if running
        if task_id in self._async_tasks:
            handle = self._async_tasks[task_id]
            if not handle.done():
                handle.cancel()
            del self._async_tasks[task_id]

        state.update_status(TaskStatus.CANCELLED, error="Task was manually cancelled by user.")
        logger.info(f"Successfully cancelled task '{task_id}'.")
        return True

    def get_task_response(self, task_id: str) -> Optional[TaskResponse]:
        """Convert AgentState into a structured TaskResponse."""
        state = self.get_task(task_id)
        if not state:
            return None

        return TaskResponse(
            task_id=state.task_id,
            user_input=state.user_input,
            status=state.status,
            created_at=state.created_at,
            updated_at=state.updated_at,
            current_step=state.current_step,
            result=state.result,
            error=state.error,
            steps=state.steps,
            metadata={"correlation_id": state.correlation_id},
        )
