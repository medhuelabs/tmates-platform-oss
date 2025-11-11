"""WebSocket endpoints for real-time communication."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.schemas import ChatMessageAttachment
from app.auth import get_auth_manager

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks live WebSocket connections per user."""

    def __init__(self) -> None:
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        await websocket.accept()
        logger.info(f"WebSocket connection accepted for user_id={user_id}")
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)
        logger.info(f"Active WebSocket connections for user {user_id}: {len(self.active_connections[user_id])}")

    def disconnect(self, websocket: WebSocket, user_id: str) -> None:
        connections = self.active_connections.get(user_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self.active_connections.pop(user_id, None)
        logger.debug(
            "Closed websocket for user=%s; remaining=%s",
            user_id,
            len(self.active_connections.get(user_id, ())),
        )

    async def send_to_user(self, user_id: str, message: Dict[str, Any]) -> None:
        """Send a message to all connections for a specific user."""
        connections = self.active_connections.get(user_id)
        if not connections:
            logger.debug(
                "Skipped websocket fanout; user=%s has no active connections", user_id
            )
            return

        payload = json.dumps(message)
        disconnected: List[WebSocket] = []
        for connection in connections.copy():
            try:
                await connection.send_text(payload)
            except Exception as exc:  # pragma: no cover - network failures
                logger.warning(
                    "Failed to relay websocket message to user=%s: %s", user_id, exc
                )
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection, user_id)


manager = ConnectionManager()


def validate_websocket_token(token: str, expected_user_id: str) -> bool:
    """Validate Supabase JWT for WebSocket connections."""
    if not token:
        return False

    try:
        auth_manager = get_auth_manager()
        payload = auth_manager.get_user_from_token(token)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("WebSocket auth token verification failed: %s", exc)
        return False

    return bool(payload and payload.get("id") == expected_user_id)


@router.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    token: str = Query(..., description="JWT token for authentication"),
) -> None:
    """WebSocket endpoint for real-time chat updates."""
    
    logger.info(f"WebSocket connection attempt for user_id={user_id}")
    
    if not validate_websocket_token(token, user_id):
        logger.warning(f"WebSocket authentication failed for user_id={user_id}")
        await websocket.close(code=1008, reason="Invalid authentication token")
        return

    logger.info(f"WebSocket authentication successful for user_id={user_id}")
    await manager.connect(websocket, user_id)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type")
            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if msg_type == "subscribe":
                thread_id = message.get("thread_id")
                await websocket.send_text(
                    json.dumps({"type": "subscribed", "thread_id": thread_id})
                )
                continue

            if msg_type == "send_message":
                logger.info(f"Processing send_message for user_id={user_id}, thread_id={message.get('thread_id')}")
                await _handle_send_message(websocket, user_id, message)
                continue

            logger.warning("Unknown WebSocket message type: %s from user_id=%s", msg_type, user_id)
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
    except Exception:
        logger.exception("WebSocket error for user=%s", user_id)
        manager.disconnect(websocket, user_id)


async def _handle_send_message(
    websocket: WebSocket, user_id: str, payload: Dict[str, Any]
) -> None:
    """Process send_message events from the client."""
    
    logger.info(f"_handle_send_message called for user_id={user_id} with payload keys: {list(payload.keys())}")

    thread_id = payload.get("thread_id")
    content = payload.get("content")
    attachments_raw = payload.get("attachments") or []
    attachments: List[Dict[str, Any]] = []

    if isinstance(attachments_raw, list):
        for entry in attachments_raw:
            if not isinstance(entry, dict):
                continue
            try:
                attachment_model = ChatMessageAttachment.model_validate(entry)
            except ValidationError as exc:
                logger.warning(
                    "Invalid attachment payload over websocket for user=%s: %s",
                    user_id,
                    exc,
                )
                continue
            attachments.append(attachment_model.model_dump(exclude_none=True))

    if not thread_id or not content:
        logger.warning(
            "Rejecting websocket payload for user=%s: missing thread_id/content",
            user_id,
        )
        await websocket.send_text(
            json.dumps({"type": "error", "message": "Missing thread_id or content"})
        )
        return

    from .chats import send_chat_message_internal  # Local import to avoid cycles

    try:
        await send_chat_message_internal(
            user_id,
            thread_id,
            content.strip(),
            attachments=attachments,
        )
    except Exception as exc:
        logger.exception(
            "Failed to process websocket send_message for user=%s thread=%s",
            user_id,
            thread_id,
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "error",
                    "message": "Failed to send message",
                    "error": str(exc),
                }
            )
        )
        return

    logger.info(f"Sending message_sent confirmation for thread_id={thread_id}, user_id={user_id}")
    await websocket.send_text(json.dumps({"type": "message_sent", "thread_id": thread_id}))


async def notify_new_message(user_id: str, thread_id: str, message: Dict[str, Any]) -> None:
    """Notify a user of a new message via WebSocket."""
    try:
        await manager.send_to_user(
            user_id,
            {"type": "new_message", "thread_id": thread_id, "message": message},
        )
    except Exception as exc:
        logger.warning(
            "Failed websocket new_message notification for user=%s: %s", user_id, exc
        )


async def notify_chat_status(
    user_id: str, thread_id: str, status: str, data: Optional[Dict[str, Any]] = None
) -> None:
    """Notify a user of chat status updates via WebSocket."""
    payload: Dict[str, Any] = {"type": "chat_status", "thread_id": thread_id, "status": status}
    if data:
        payload.update(data)

    try:
        await manager.send_to_user(user_id, payload)
    except Exception as exc:
        logger.warning(
            "Failed websocket chat_status notification for user=%s: %s", user_id, exc
        )


async def notify_file_generated(user_id: str, file_info: Dict[str, Any]) -> None:
    """Notify a user when a file has been generated."""
    try:
        await manager.send_to_user(user_id, {"type": "file_generated", "file": file_info})
    except Exception as exc:
        logger.warning(
            "Failed websocket file_generated notification for user=%s: %s", user_id, exc
        )


async def notify_pinboard_post(user_id: str, post: Dict[str, Any]) -> None:
    """Notify a user when a pinboard post is created."""
    try:
        await manager.send_to_user(user_id, {"type": "pinboard_post", "post": post})
    except Exception as exc:
        logger.warning(
            "Failed websocket pinboard notification for user=%s: %s", user_id, exc
        )
