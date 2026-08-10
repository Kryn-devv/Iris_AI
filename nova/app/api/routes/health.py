"""Health and status endpoints for NOVA API."""

from fastapi import APIRouter, Depends
from typing import Dict, Any
from nova.app.core.config import settings
from nova.app.api.dependencies import get_tool_registry, get_model_gateway
from nova.app.tools.registry import ToolRegistry
from nova.app.llm.gateway import ModelGateway

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Basic health check")
async def health_check() -> Dict[str, str]:
    """Check API server health."""
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


@router.get("/api/v1/status", summary="Detailed system status")
async def system_status(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    model_gateway: ModelGateway = Depends(get_model_gateway),
) -> Dict[str, Any]:
    """Get detailed component and model status."""
    provider = model_gateway.get_provider()
    provider_healthy = await provider.health_check()

    return {
        "app_name": settings.APP_NAME,
        "version": "0.1.0",
        "phase": "Phase 1 - Kernel Foundation",
        "status": "healthy",
        "offline_mode": True,
        "llm_provider": {
            "active_provider": provider.provider_name,
            "default_model": provider.default_model,
            "healthy": provider_healthy,
        },
        "registered_tools": [tool.name for tool in tool_registry.list_tools()],
        "limits": {
            "max_planning_iterations": settings.MAX_PLANNING_ITERATIONS,
            "max_tool_calls": settings.MAX_TOOL_CALLS,
            "per_tool_timeout_seconds": settings.PER_TOOL_TIMEOUT_SECONDS,
            "total_task_timeout_seconds": settings.TOTAL_TASK_TIMEOUT_SECONDS,
        },
    }
