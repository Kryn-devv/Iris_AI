"""Chat endpoints: message handling and confirmation round-trips."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from iris.app.agent.kernel import AgentKernel
from iris.app.api.dependencies import get_agent_kernel
from iris.app.schemas.messages import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/v1", tags=["Chat"])


@router.post("/chat", response_model=ChatResponse, summary="Send a chat message to IRIS")
async def chat_endpoint(
    request: ChatRequest,
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> ChatResponse:
    """Process user input through the IRIS kernel pipeline."""
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message field cannot be empty.")

    return await kernel.process_request(
        user_input=request.message,
        conversation_id=request.conversation_id,
        user_approved=request.user_approved,
        channel=request.channel,
    )


class ConfirmRequest(BaseModel):
    """Approve or reject a pending tool action."""

    task_id: str
    approved: bool


@router.post("/chat/confirm", response_model=ChatResponse, summary="Resolve a pending confirmation")
async def confirm_endpoint(
    request: ConfirmRequest,
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> ChatResponse:
    """Resume a task that paused waiting for user confirmation."""
    try:
        return await kernel.resume_task_confirmation(request.task_id, request.approved)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
