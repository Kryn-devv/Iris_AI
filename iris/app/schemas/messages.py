"""Pydantic schemas for API message exchanges."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Payload for POST /api/v1/chat."""

    message: str
    conversation_id: Optional[str] = None
    user_approved: bool = False
    #: Where the request came from: "web" | "voice" | "phone" | "telegram" | "api".
    channel: str = "web"
    #: Client asks for a speakable answer (voice clients set this).
    speak: bool = False
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
    #: Short sentence for text-to-speech (falls back to ``response``).
    speech: Optional[str] = None
    intent_detected: Optional[str] = None
    #: "nlu" when the deterministic engine handled it, "agent" for the LLM loop,
    #: "memory" for memory commands, "smalltalk" for the personality layer.
    handler: Optional[str] = None
    tools_executed: List[ToolExecutionSummary] = Field(default_factory=list)
    #: Files produced during handling (absolute paths).
    artifacts: List[str] = Field(default_factory=list)
    #: Structured hints for the UI.
    ui: Dict[str, Any] = Field(default_factory=dict)
    status: str = "COMPLETED"
    provider: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None
    language: Optional[str] = None
    response_language: Optional[str] = None
    #: Pending confirmation details when status is WAITING_FOR_CONFIRMATION.
    pending_action: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
