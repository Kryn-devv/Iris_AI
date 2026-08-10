"""Pydantic schemas for tools and tool executions."""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from nova.app.core.security import PermissionLevel


class ToolParameterSchema(BaseModel):
    """Schema defining expected inputs for a tool."""
    type: str = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class ToolMetadata(BaseModel):
    """Metadata describing a tool registered in NOVA."""
    name: str
    description: str
    permission_level: PermissionLevel
    input_schema: ToolParameterSchema
    output_schema: Optional[Dict[str, Any]] = None


class ToolExecutionRequest(BaseModel):
    """Payload for invoking a tool."""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    user_approved: bool = False


class ToolExecutionResult(BaseModel):
    """Standard result structure returned by a tool execution."""
    tool_name: str
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    execution_time_seconds: float = 0.0
