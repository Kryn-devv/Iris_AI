"""Pydantic schemas for API message exchanges."""

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Payload for POST /api/v1/chat."""
    message: str
    conversation_id: Optional[str] = None
    user_approved: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolExecutionSummary(BaseModel):
    """Summary of a tool executed during chat handling."""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None


class ChatResponse(BaseModel):
    """Response returned from POST /api/v1/chat."""
    task_id: str
    correlation_id: str
    response: str
    intent_detected: Optional[str] = None
    tools_executed: List[ToolExecutionSummary] = Field(default_factory=list)
    status: str = "COMPLETED"
    provider: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    language: Optional[str] = None
    response_language: Optional[str] = None
    error: Optional[str] = None
