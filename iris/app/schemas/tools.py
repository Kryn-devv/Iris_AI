"""Pydantic schemas for tools and tool executions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from iris.app.core.security import PermissionLevel


class ToolCategory:
    """Canonical tool category identifiers used for grouping in the UI."""

    CORE = "core"
    DESKTOP = "desktop"
    SYSTEM = "system"
    FILES = "files"
    WEB = "web"
    CONTENT = "content"
    CODE = "code"
    MEDIA = "media"
    AUTOMATION = "automation"
    KNOWLEDGE = "knowledge"
    COMMUNICATION = "communication"

    ALL = (
        CORE, DESKTOP, SYSTEM, FILES, WEB, CONTENT,
        CODE, MEDIA, AUTOMATION, KNOWLEDGE, COMMUNICATION,
    )


class ToolParameterSchema(BaseModel):
    """JSON-Schema fragment describing the expected inputs for a tool."""

    type: str = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)


class ToolExample(BaseModel):
    """A natural-language example of invoking a tool, shown in help and the UI."""

    utterance: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolMetadata(BaseModel):
    """Metadata describing a tool registered in IRIS."""

    name: str
    description: str
    permission_level: PermissionLevel
    input_schema: ToolParameterSchema
    output_schema: Optional[Dict[str, Any]] = None
    category: str = ToolCategory.CORE
    aliases: List[str] = Field(default_factory=list)
    #: Capability identifiers from ``iris.app.core.platform_info`` this tool needs.
    required_capabilities: List[str] = Field(default_factory=list)
    #: Operating systems the tool supports; empty means "all".
    os_support: List[str] = Field(default_factory=list)
    #: False when an optional dependency or the host OS makes it unusable.
    available: bool = True
    unavailable_reason: Optional[str] = None
    #: True when the tool reaches the network.
    network: bool = False
    #: True when the tool mutates the user's machine.
    mutating: bool = False
    examples: List[ToolExample] = Field(default_factory=list)


class ToolExecutionRequest(BaseModel):
    """Payload for invoking a tool directly."""

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
    #: Short sentence suitable for text-to-speech ("Opened YouTube.").
    speech: Optional[str] = None
    #: Longer human-readable text for the chat transcript.
    display: Optional[str] = None
    #: Files produced by the tool (absolute paths).
    artifacts: List[str] = Field(default_factory=list)
    #: Structured hints for the UI (e.g. render a table, open a preview).
    ui: Dict[str, Any] = Field(default_factory=dict)

    def spoken_or_display(self) -> str:
        """Best single-line summary of this result."""
        if self.speech:
            return self.speech
        if self.display:
            return self.display
        if self.error:
            return self.error
        if isinstance(self.result, dict):
            for key in ("formatted", "summary", "message", "result"):
                value = self.result.get(key)
                if isinstance(value, str) and value:
                    return value
        return "" if self.result is None else str(self.result)
