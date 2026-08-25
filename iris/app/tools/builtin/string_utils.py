"""String manipulation tool for safe text operations."""

from typing import Any, Dict
from iris.app.tools.base import BaseTool
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolParameterSchema


class StringUtilsTool(BaseTool):
    """Tool for performing safe string transformations."""

    name = "string_utils"
    description = "Performs safe text operations such as uppercase, lowercase, length count, or replace."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "text": {
                "type": "string",
                "description": "The input text to operate on.",
            },
            "operation": {
                "type": "string",
                "description": "Operation to perform: 'uppercase', 'lowercase', 'length', or 'replace'.",
            },
            "old": {
                "type": "string",
                "description": "Sub-string to find (required for 'replace' operation).",
            },
            "new": {
                "type": "string",
                "description": "Sub-string to replace with (required for 'replace' operation).",
            },
        },
        required=["text", "operation"],
    )

    async def _run(
        self,
        text: str = "",
        operation: str = "uppercase",
        old: str = "",
        new: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        op = operation.lower().strip()
        if op == "uppercase":
            res = text.upper()
        elif op == "lowercase":
            res = text.lower()
        elif op == "length":
            res = len(text)
        elif op == "replace":
            res = text.replace(old, new)
        else:
            raise ValueError(f"Unsupported string operation: '{operation}'")

        return {
            "operation": op,
            "original": text,
            "result": res,
        }
