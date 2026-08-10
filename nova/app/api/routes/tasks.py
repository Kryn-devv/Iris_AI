"""Task management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from nova.app.schemas.tasks import TaskCreate, TaskResponse
from nova.app.agent.task_manager import TaskManager
from nova.app.agent.kernel import AgentKernel
from nova.app.api.dependencies import get_task_manager, get_agent_kernel

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


@router.post("", response_model=TaskResponse, summary="Create and run a background task")
async def create_task_endpoint(
    payload: TaskCreate,
    kernel: AgentKernel = Depends(get_agent_kernel),
    task_manager: TaskManager = Depends(get_task_manager),
) -> TaskResponse:
    """Create task and run via agent kernel."""
    if not payload.user_input or not payload.user_input.strip():
        raise HTTPException(status_code=400, detail="user_input field cannot be empty.")

    # Process task synchronously or convert to response
    res = await kernel.process_request(user_input=payload.user_input)
    task_resp = task_manager.get_task_response(res.task_id)
    if not task_resp:
        raise HTTPException(status_code=500, detail="Failed to retrieve task response after execution.")
    return task_resp


@router.get("/{task_id}", response_model=TaskResponse, summary="Get task status and step history")
async def get_task_endpoint(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
) -> TaskResponse:
    """Get status and step records for a given task_id."""
    task_resp = task_manager.get_task_response(task_id)
    if not task_resp:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task_resp


@router.post("/{task_id}/cancel", summary="Cancel an ongoing task")
async def cancel_task_endpoint(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
) -> Dict[str, Any]:
    """Cancel task execution."""
    success = await task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail=f"Task '{task_id}' could not be cancelled or does not exist.")
    return {"task_id": task_id, "status": "CANCELLED", "success": True}


@router.post("/{task_id}/confirm", summary="Approve and resume a task waiting for confirmation")
async def confirm_task_endpoint(
    task_id: str,
    kernel: AgentKernel = Depends(get_agent_kernel),
    task_manager: TaskManager = Depends(get_task_manager),
) -> TaskResponse:
    """Approve a pending tool action and resume task loop."""
    try:
        res = await kernel.resume_task_confirmation(task_id, approved=True)
        task_resp = task_manager.get_task_response(task_id)
        return task_resp or TaskResponse(
            task_id=task_id,
            user_input="",
            status=res.status,
            created_at=None,
            updated_at=None,
            result=res.response,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to confirm task '{task_id}': {e}")


@router.post("/{task_id}/reject", summary="Reject a pending action for a task waiting for confirmation")
async def reject_task_endpoint(
    task_id: str,
    kernel: AgentKernel = Depends(get_agent_kernel),
    task_manager: TaskManager = Depends(get_task_manager),
) -> TaskResponse:
    """Reject a pending tool action and resume task loop."""
    try:
        res = await kernel.resume_task_confirmation(task_id, approved=False)
        task_resp = task_manager.get_task_response(task_id)
        return task_resp or TaskResponse(
            task_id=task_id,
            user_input="",
            status=res.status,
            created_at=None,
            updated_at=None,
            result=res.response,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reject task '{task_id}': {e}")
