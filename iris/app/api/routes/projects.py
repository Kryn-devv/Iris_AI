"""Project-scoped memory REST API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Any, List
from iris.app.agent.kernel import AgentKernel
from iris.app.api.dependencies import get_agent_kernel
from iris.app.schemas.memory import MemoryType, MemoryCreatePayload

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


@router.post("/{project_id}/memory", summary="Store project-scoped memory")
async def store_project_memory_endpoint(
    project_id: str,
    payload: MemoryCreatePayload,
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> Dict[str, Any]:
    """Store key-value pair under project context."""
    meta = payload.model_dump()
    meta["project_id"] = project_id

    await kernel.memory_service.remember(
        key=payload.key,
        value=payload.value,
        memory_type=MemoryType.PROJECT,
        metadata=meta,
    )
    return {"status": "success", "project_id": project_id, "key": payload.key}


@router.get("/{project_id}/memory", summary="Retrieve project-scoped memory entries")
async def get_project_memory_endpoint(
    project_id: str,
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> Dict[str, Any]:
    """Retrieve all active memory records for a project."""
    records = await kernel.memory_service.project_memory.get_project_records(project_id)
    return {
        "project_id": project_id,
        "total_records": len(records),
        "records": [r.model_dump() for r in records],
    }
