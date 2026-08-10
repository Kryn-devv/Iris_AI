"""Memory management endpoints."""

from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Dict, Any, Optional, List
from nova.app.agent.kernel import AgentKernel
from nova.app.api.dependencies import get_agent_kernel
from nova.app.schemas.memory import MemoryType, MemoryCreatePayload, MemorySearchQuery, ConfidenceLevel
from nova.app.memory.sanitizer import MemorySanitizer

router = APIRouter(prefix="/api/v1/memory", tags=["Memory"])


@router.get("", summary="Inspect memory contents")
async def get_memory_endpoint(
    memory_type: str = Query("working", description="working, semantic, project, episodic, conversation, or long_term"),
    key: Optional[str] = Query(None, description="Memory key to retrieve"),
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> Dict[str, Any]:
    """Retrieve stored memory contents."""
    m_service = kernel.memory_service
    
    if key:
        val = await m_service.retrieve(key, memory_type=MemoryType(memory_type) if memory_type in MemoryType.__members__.values() else None)
        return {"key": key, "memory_type": memory_type, "value": val}

    if memory_type == "working":
        return {"contents": m_service.working_memory.dump()}
    elif memory_type == "project":
        return {"contents": m_service.project_memory.get_all()}
    elif memory_type == "semantic":
        recs = await m_service.semantic_memory.list_records()
        return {"contents": [r.model_dump() for r in recs]}
    elif memory_type == "episodic":
        recs = await m_service.episodic_memory.get_events()
        return {"contents": [r.model_dump() for r in recs]}
    elif memory_type == "long_term" or memory_type == "conversation":
        return {"contents": {}}

    raise HTTPException(status_code=400, detail=f"Unsupported memory_type '{memory_type}'")


@router.post("", summary="Store item in memory")
async def store_memory_endpoint(
    payload: MemoryCreatePayload,
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> Dict[str, Any]:
    """Save key-value pair to specified memory layer."""
    m_service = kernel.memory_service
    target_type = payload.get_memory_type()
    await m_service.remember(
        key=payload.key,
        value=payload.value,
        memory_type=target_type,
        metadata=payload.model_dump(),
    )
    return {"status": "success", "memory_type": target_type.value, "key": payload.key}


@router.get("/search", summary="Search memory entries with relevance scoring")
async def search_memory_endpoint(
    query: str = Query(..., description="Search query string"),
    memory_type: Optional[str] = Query(None, description="Optional memory type filter"),
    project_id: Optional[str] = Query(None, description="Optional project filter"),
    limit: int = Query(5, ge=1, le=50),
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> Dict[str, Any]:
    """Search stored memories using relevance scoring."""
    m_type = MemoryType(memory_type) if memory_type and memory_type in [t.value for t in MemoryType] else None
    results = await kernel.memory_service.search(
        query=query,
        memory_type=m_type,
        project_id=project_id,
        limit=limit,
    )
    
    formatted = [
        {
            "record": rec.model_dump(),
            "relevance_score": score,
        }
        for rec, score in results
    ]
    return {"query": query, "total_found": len(formatted), "results": formatted}


@router.delete("/{key}", summary="Forget/delete memory entry by key")
async def forget_memory_endpoint(
    key: str,
    memory_type: Optional[str] = Query(None),
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> Dict[str, Any]:
    """Delete or mark memory record as superseded."""
    m_type = MemoryType(memory_type) if memory_type and memory_type in [t.value for t in MemoryType] else None
    deleted = await kernel.memory_service.forget(key, memory_type=m_type)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory record with key '{key}' not found.")
    return {"status": "success", "key": key, "forgotten": True}
