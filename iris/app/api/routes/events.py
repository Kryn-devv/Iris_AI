"""Realtime event streaming: WebSocket (primary) and SSE (fallback).

The WebSocket is bidirectional — clients receive every bus event (agent
activity, tool progress, voice state, reminders) and can also send chat
messages over the same socket, which makes the phone/web UI a single
connection."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from iris.app.agent.kernel import default_kernel
from iris.app.core.auth import auth_required, is_loopback, verify_token
from iris.app.core.bus import default_event_bus
from iris.app.core.logging import get_logger

router = APIRouter(prefix="/api/v1", tags=["Events"])
logger = get_logger("api.events")


def _ws_authorized(websocket: WebSocket, token: Optional[str]) -> bool:
    client_host = websocket.client.host if websocket.client else None
    if not auth_required() or is_loopback(client_host):
        return True
    return verify_token(token)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = Query(default=None)) -> None:
    """Bidirectional realtime channel for the UI and phone clients."""
    if not _ws_authorized(websocket, token):
        await websocket.close(code=4401, reason="Unauthorized")
        return

    await websocket.accept()
    subscription = default_event_bus.subscribe()
    logger.info("WebSocket client connected (%s subscribers).", default_event_bus.subscriber_count)

    async def pump_events() -> None:
        async for event in subscription:
            await websocket.send_text(json.dumps({"type": "event", **event.to_dict()}, default=str))

    pump_task = asyncio.create_task(pump_events())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"type": "chat", "message": raw}

            msg_type = payload.get("type", "chat")
            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue
            if msg_type == "chat":
                message = (payload.get("message") or "").strip()
                if not message:
                    continue
                response = await default_kernel.process_request(
                    user_input=message,
                    conversation_id=payload.get("conversation_id"),
                    user_approved=bool(payload.get("user_approved")),
                    channel=payload.get("channel", "ws"),
                )
                await websocket.send_text(
                    json.dumps({"type": "response", **response.model_dump()}, default=str)
                )
            elif msg_type == "confirm":
                try:
                    response = await default_kernel.resume_task_confirmation(
                        payload.get("task_id", ""), bool(payload.get("approved"))
                    )
                    await websocket.send_text(
                        json.dumps({"type": "response", **response.model_dump()}, default=str)
                    )
                except ValueError as exc:
                    await websocket.send_text(json.dumps({"type": "error", "detail": str(exc)}))
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        default_event_bus.unsubscribe(subscription)
        logger.info("WebSocket client disconnected.")


@router.get("/events", summary="Server-sent events stream (fallback for WebSocket)")
async def sse_endpoint() -> StreamingResponse:
    """SSE stream of all bus events."""
    subscription = default_event_bus.subscribe()

    async def generate():
        try:
            # Replay a little recent history so late joiners see context.
            for event in default_event_bus.history(limit=20):
                yield f"data: {json.dumps(event.to_dict(), default=str)}\n\n"
            async for event in subscription:
                yield f"data: {json.dumps(event.to_dict(), default=str)}\n\n"
        finally:
            default_event_bus.unsubscribe(subscription)

    return StreamingResponse(generate(), media_type="text/event-stream")
