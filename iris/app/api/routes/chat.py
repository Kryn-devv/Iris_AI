"""Chat endpoint for executing requests through Agent Kernel."""

from fastapi import APIRouter, Depends, HTTPException
from iris.app.schemas.messages import ChatRequest, ChatResponse
from iris.app.agent.kernel import AgentKernel
from iris.app.api.dependencies import get_agent_kernel

router = APIRouter(prefix="/api/v1", tags=["Chat"])


@router.post("/chat", response_model=ChatResponse, summary="Send a chat message to IRIS")
async def chat_endpoint(
    request: ChatRequest,
    kernel: AgentKernel = Depends(get_agent_kernel),
) -> ChatResponse:
    """Process user prompt through the IRIS Agent Kernel lifecycle."""
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message field cannot be empty.")

    response = await kernel.process_request(
        user_input=request.message,
        conversation_id=request.conversation_id,
        user_approved=request.user_approved,
    )
    return response
