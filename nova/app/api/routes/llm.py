"""LLM Status & Provider management endpoint."""

from fastapi import APIRouter, Depends
from typing import Dict, Any
from nova.app.llm.gateway import ModelGateway
from nova.app.api.dependencies import get_model_gateway

router = APIRouter(prefix="/api/v1/llm", tags=["LLM"])


@router.get("/status", summary="Get LLM mode, provider health, latency, and capabilities")
async def get_llm_status(
    model_gateway: ModelGateway = Depends(get_model_gateway),
) -> Dict[str, Any]:
    """Retrieve operational status of LLM providers without leaking credentials."""
    return await model_gateway.get_llm_status()
