"""Tool Schema Adapter for converting IRIS tools to OpenAI-compatible definitions.

Schemas are aggressively compacted: with 60+ tools attached to every agent
request, verbose descriptions blow straight through free-tier token budgets
(Groq allows 8k tokens/minute — the uncompacted catalogue alone was ~6.4k).
Compaction keeps everything a model needs to call the tool correctly (names,
types, enums, required flags, first-sentence descriptions) and drops prose.
"""

from __future__ import annotations

from typing import Any, Dict, List

from iris.app.schemas.tools import ToolMetadata
from iris.app.tools.base import BaseTool

#: Character budget for a function description (first sentence usually fits).
_FN_DESC_LIMIT = 110
#: Character budget for each parameter description.
_PARAM_DESC_LIMIT = 70
#: JSON-Schema keys that matter for correct tool calls; everything else is prose.
_KEEP_KEYS = ("type", "enum", "items", "properties", "required", "minimum", "maximum", "format")


def _trim(text: str, limit: int) -> str:
    """First sentence of ``text``, hard-capped at ``limit`` characters."""
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        period = text.find(". ")
        if 0 < period < limit:
            return text[: period + 1]
        return text[: limit - 1].rstrip() + "…"
    return text


def _compact_schema(schema: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    """Recursively keep only call-relevant JSON-Schema keys."""
    out: Dict[str, Any] = {}
    for key in _KEEP_KEYS:
        if key not in schema:
            continue
        value = schema[key]
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _compact_schema(sub, depth + 1) for name, sub in value.items() if isinstance(sub, dict)}
        elif key == "items" and isinstance(value, dict):
            out[key] = _compact_schema(value, depth + 1)
        else:
            out[key] = value
    description = schema.get("description")
    if description:
        out["description"] = _trim(description, _PARAM_DESC_LIMIT)
    return out


class ToolSchemaAdapter:
    """Converts internal IRIS Tool definitions to OpenAI function tool schemas."""

    @staticmethod
    def to_openai_function(metadata: ToolMetadata) -> Dict[str, Any]:
        """Convert ToolMetadata to a compact OpenAI-compatible function definition."""
        return {
            "type": "function",
            "function": {
                "name": metadata.name,
                "description": _trim(metadata.description, _FN_DESC_LIMIT),
                "parameters": {
                    "type": metadata.input_schema.type,
                    "properties": {
                        name: _compact_schema(sub) if isinstance(sub, dict) else sub
                        for name, sub in metadata.input_schema.properties.items()
                    },
                    "required": metadata.input_schema.required,
                },
            },
        }

    @classmethod
    def from_tool(cls, tool: BaseTool) -> Dict[str, Any]:
        """Convert a BaseTool instance directly to OpenAI function schema."""
        return cls.to_openai_function(tool.get_metadata())

    @classmethod
    def convert_many(cls, tools: List[ToolMetadata]) -> List[Dict[str, Any]]:
        """Convert a list of ToolMetadata items."""
        return [cls.to_openai_function(t) for t in tools]
