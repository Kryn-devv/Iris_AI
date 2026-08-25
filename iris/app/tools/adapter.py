"""Tool Schema Adapter for converting IRIS tools to OpenAI-compatible definitions."""

from typing import Dict, Any, List
from iris.app.schemas.tools import ToolMetadata
from iris.app.tools.base import BaseTool


class ToolSchemaAdapter:
    """Converts internal IRIS Tool definitions to OpenAI function tool schemas."""

    @staticmethod
    def to_openai_function(metadata: ToolMetadata) -> Dict[str, Any]:
        """Convert ToolMetadata to an OpenAI-compatible function definition."""
        return {
            "type": "function",
            "function": {
                "name": metadata.name,
                "description": metadata.description,
                "parameters": {
                    "type": metadata.input_schema.type,
                    "properties": metadata.input_schema.properties,
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
