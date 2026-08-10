"""Pydantic schemas for Agent Kernel planning, steps, and state."""

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from nova.app.schemas.tasks import TaskStatus


class PlanStep(BaseModel):
    """Single step in an Agent plan."""
    step_id: int
    action: str  # "tool_call" or "final_response"
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None


class AgentPlan(BaseModel):
    """Complete multi-step plan produced by Planner."""
    user_intent: str
    steps: List[PlanStep] = Field(default_factory=list)
    requires_confirmation: bool = False


class AgentExecutionState(BaseModel):
    """Runtime execution snapshot held by the Agent Kernel."""
    task_id: str
    correlation_id: str
    user_input: str
    status: TaskStatus = TaskStatus.PENDING
    current_iteration: int = 0
    plan: Optional[AgentPlan] = None
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    tool_call_count: int = 0
    final_output: Optional[str] = None
    error: Optional[str] = None
