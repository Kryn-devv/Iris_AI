"""Agent execution state manager."""

from typing import Dict, Any, Optional, List
import uuid
from datetime import datetime
from nova.app.schemas.tasks import TaskStatus, TaskStepRecord
from nova.app.schemas.agent import AgentPlan


class AgentState:
    """Encapsulates active state for a single execution task."""

    def __init__(
        self,
        user_input: str,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_approved: bool = False,
    ):
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        self.correlation_id = correlation_id or f"cid_{uuid.uuid4().hex[:12]}"
        self.conversation_id = conversation_id
        self.user_input = user_input
        self.user_approved = user_approved
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.current_step = 0
        self.iteration_count = 0
        self.tool_call_count = 0
        self.planning_iterations = 0
        self.replanning_count = 0
        self.plan: Optional[AgentPlan] = None
        self.steps: List[TaskStepRecord] = []
        self.observations: List[Dict[str, Any]] = []
        self.pending_tool_call: Optional[Dict[str, Any]] = None
        self.provider: Optional[str] = None
        self.model: Optional[str] = None
        self.result: Optional[Any] = None
        self.error: Optional[str] = None
        self.metadata: Dict[str, Any] = {}

    def record_step(
        self,
        step_type: str,
        description: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> TaskStepRecord:
        """Record a completed step in task history."""
        self.current_step += 1
        record = TaskStepRecord(
            step_number=self.current_step,
            step_type=step_type,
            description=description,
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            error=error,
            timestamp=datetime.now(),
        )
        self.steps.append(record)
        self.updated_at = datetime.now()
        return record

    def update_status(self, status: TaskStatus, error: Optional[str] = None) -> None:
        """Update overall task status."""
        self.status = status
        if error:
            self.error = error
        self.updated_at = datetime.now()
