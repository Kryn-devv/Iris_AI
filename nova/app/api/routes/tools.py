"""Tool registry discovery endpoints."""

from fastapi import APIRouter, Depends
from typing import List
from nova.app.schemas.tools import ToolMetadata
from nova.app.tools.registry import ToolRegistry
from nova.app.api.dependencies import get_tool_registry

router = APIRouter(prefix="/api/v1/tools", tags=["Tools"])


@router.get("", response_model=List[ToolMetadata], summary="List all registered tools")
async def list_tools_endpoint(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> List[ToolMetadata]:
    """Retrieve metadata for all tools registered in the ToolRegistry."""
    return tool_registry.list_tools()
