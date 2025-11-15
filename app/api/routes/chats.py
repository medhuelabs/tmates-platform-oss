"""Chat and messaging endpoints for mobile clients."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError

from app.api.dependencies import get_database_with_user
from app.api.schemas import (
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageCreate,
    ChatSessionResetResponse,
    ChatThread,
    ChatThreadSummary,
)
from app.core.agent_runner import resolve_user_context
from app.db import TransientDatabaseError
from app.services.team_chat_dispatcher import TeamDispatchResult, team_chat_dispatcher
from .websocket import notify_new_message

router = APIRouter()

MAX_MESSAGE_PREVIEW = 160


def _raise_transient_chat_error(exc: TransientDatabaseError) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Chat data temporarily unavailable. Please try again.",
    ) from exc


def _normalize_agent_keys(raw_value) -> List[str]:
    if isinstance(raw_value, list):
        return [str(entry) for entry in raw_value if entry]
    if isinstance(raw_value, str):
        return [value.strip() for value in raw_value.split(",") if value.strip()]
    return []


def _coerce_thread_title(thread: Dict[str, object]) -> str:
    title = thread.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "Team Chat"


def _build_preview(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= MAX_MESSAGE_PREVIEW:
        return cleaned
    return cleaned[: MAX_MESSAGE_PREVIEW - 1].rstrip() + "…"


def _convert_attachment(entry: Dict[str, object]) -> Optional[ChatMessageAttachment]:
    uri = entry.get("uri") or entry.get("url") or entry.get("download_url")
    if not uri:
        return None

    def _to_int(value: object) -> Optional[int]:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    def _to_float(value: object) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    attachment_type = entry.get("type") or entry.get("mime_type") or entry.get("content_type")
    name = entry.get("name") or entry.get("label") or entry.get("filename")
    relative_path = entry.get("relative_path")
    download_url = entry.get("download_url")

    return ChatMessageAttachment(
        uri=str(uri),
        type=str(attachment_type) if attachment_type else None,
        name=str(name) if name else None,
        relative_path=str(relative_path) if isinstance(relative_path, str) else None,
        download_url=str(download_url) if isinstance(download_url, str) else None,
        size_bytes=_to_int(entry.get("size_bytes")),
        width=_to_int(entry.get("width")),
        height=_to_int(entry.get("height")),
        duration=_to_float(entry.get("duration")),
    )


def _convert_message(record: Dict[str, object]) -> ChatMessage:
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    attachments_raw = payload.get("attachments") or []
    attachments: List[ChatMessageAttachment] = []
    if isinstance(attachments_raw, list):
        for entry in attachments_raw:
            if isinstance(entry, dict):
                attachment = _convert_attachment(entry)
                if attachment:
                    attachments.append(attachment)
    session_id = record.get("session_id")
    if session_id is not None:
        session_id = str(session_id)
    elif payload.get("session_id"):
        candidate = payload.get("session_id")
        if isinstance(candidate, str):
            session_id = candidate
    return ChatMessage(
        id=str(record.get("id")),
        role=str(record.get("role") or "assistant"),
        content=str(record.get("content") or ""),
        author=record.get("author"),
        created_at=record.get("created_at"),
        payload=payload,
        attachments=attachments,
        session_id=session_id,
    )


def _convert_summary(
    record: Dict[str, object],
    last_message: Optional[ChatMessage],
) -> ChatThreadSummary:
    agent_keys = _normalize_agent_keys(record.get("agent_keys"))
    last_activity = record.get("updated_at") or (last_message.created_at if last_message else None)
    last_preview = _build_preview(last_message.content if last_message else None)
    active_session = record.get("active_session_id")
    if active_session is not None:
        active_session = str(active_session)
    return ChatThreadSummary(
        id=str(record.get("id")),
        title=str(record.get("title") or "Conversation"),
        kind=str(record.get("kind") or "agent"),
        agent_keys=agent_keys,
        last_message_preview=last_preview,
        last_activity=last_activity,
        unread_count=0,
        active_session_id=active_session,
    )


def _ensure_thread_access(thread: Dict[str, object], user_id: str) -> None:
    owner = thread.get("user_id")
    if owner and str(owner) != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )


async def _emit_dispatcher_decline_message(
    *,
    db,
    thread_id: str,
    organization_id: Optional[str],
    user_id: str,
) -> None:
    """Insert a brief system notice when no teammate is available."""

    try:
        system_record = db.insert_chat_message(
            thread_id=thread_id,
            role="system",
            content="No teammate is available to help with that request right now.",
            author=None,
            payload={"event": "dispatcher_decline"},
            organization_id=organization_id,
            user_id=user_id,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        print(f"Failed to insert dispatcher decline message: {exc}")
        return

    if not system_record:
        return

    db.touch_chat_thread(thread_id)

    try:
        system_message = _convert_message(system_record)
        notification = {
            "id": system_message.id,
            "role": system_message.role,
            "content": system_message.content,
            "author": system_message.author,
            "created_at": system_message.created_at,
            "attachments": [],
        }
        if hasattr(notification["created_at"], "isoformat"):
            notification["created_at"] = notification["created_at"].isoformat()
        await notify_new_message(user_id, thread_id, notification)
    except Exception as ws_error:
        print(f"Failed to broadcast dispatcher decline message: {ws_error}")


@router.get("/chats", response_model=List[ChatThreadSummary], status_code=status.HTTP_200_OK)
def list_chat_threads(
    context=Depends(get_database_with_user),
    limit: int = Query(default=50, ge=1, le=100),
) -> List[ChatThreadSummary]:
    """Return recent chat threads for the authenticated user, filtered by enabled agents."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        user_context, organization, enabled_agents = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    threads = db.list_chat_threads(
        user_id,
        organization_id=organization.get("id"),
        limit=limit,
    )
    
    # Filter threads to only show those for enabled agents
    filtered_threads = []
    for thread in threads or []:
        agent_keys = _normalize_agent_keys(thread.get("agent_keys", []))
        
        # Include thread if:
        # 1. It's a group chat (has multiple agents)
        # 2. It has at least one enabled agent
        # 3. It has no agent_keys but title suggests it's for an enabled agent
        if len(agent_keys) > 1:  # Group chat
            # Only include if it has at least one enabled agent
            if any(agent in enabled_agents for agent in agent_keys):
                filtered_threads.append(thread)
        elif len(agent_keys) == 1:  # Individual agent chat
            # Only include if the agent is enabled
            if agent_keys[0] in enabled_agents:
                filtered_threads.append(thread)
        elif len(agent_keys) == 0:  # No agent_keys - check title
            title = thread.get("title", "").lower()
            # Only include if title matches an enabled agent
            if any(agent.lower() in title for agent in enabled_agents):
                filtered_threads.append(thread)
            # Special case: include "group" threads even if they have no agent_keys yet
            elif "group" in title:
                filtered_threads.append(thread)
    
    summaries: List[ChatThreadSummary] = []
    for thread in filtered_threads:
        try:
            last_records = db.list_chat_messages(
                thread_id=thread.get("id"),
                limit=1,
                ascending=False,
            )
        except TransientDatabaseError as exc:
            _raise_transient_chat_error(exc)
        last_message = _convert_message(last_records[0]) if last_records else None
        summaries.append(_convert_summary(thread, last_message))
    return summaries


@router.post("/chats", response_model=ChatThreadSummary, status_code=status.HTTP_201_CREATED)
def create_chat_thread(
    agent_key: str = Query(..., description="The agent key to create a chat with"),
    context=Depends(get_database_with_user),
) -> ChatThreadSummary:
    """Create a new chat thread with a specific agent."""
    
    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        user_context, organization, enabled_agents = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    
    # Verify the agent is enabled for this user
    if agent_key not in enabled_agents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent '{agent_key}' is not available for this user"
        )
    
    # Check if a thread already exists for this agent
    existing_threads = db.list_chat_threads(
        user_id,
        organization_id=organization.get("id"),
        limit=100,
    )
    
    for thread in existing_threads or []:
        thread_agent_keys = _normalize_agent_keys(thread.get("agent_keys", []))
        if len(thread_agent_keys) == 1 and thread_agent_keys[0] == agent_key:
            # Thread already exists, return it
            try:
                last_records = db.list_chat_messages(
                    thread_id=thread.get("id"),
                    limit=1,
                    ascending=False,
                )
            except TransientDatabaseError as exc:
                _raise_transient_chat_error(exc)
            last_message = _convert_message(last_records[0]) if last_records else None
            return _convert_summary(thread, last_message)
    
    # Create new thread
    from app.registry.agents.store import AgentStore
    agent_store = AgentStore()
    agent_def = agent_store.get_agent(agent_key)
    agent_name = agent_def.name if agent_def else agent_key.title()
    
    thread = db.create_chat_thread(
        auth_user_id=user_id,
        organization_id=organization.get("id"),
        title=agent_name,
        kind="agent",
        agent_keys=[agent_key],
        metadata={"agent_key": agent_key, "created_via": "mobile_api"}
    )
    
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create chat thread"
        )
    
    return _convert_summary(thread, None)


@router.get("/chats/{thread_id}", response_model=ChatThread, status_code=status.HTTP_200_OK)
def get_chat_thread(
    thread_id: str,
    context=Depends(get_database_with_user),
    limit: int = Query(default=200, ge=1, le=500),
) -> ChatThread:
    """Return a chat thread with recent messages."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        thread = db.get_chat_thread(thread_id)
    except TransientDatabaseError as exc:
        _raise_transient_chat_error(exc)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )
    _ensure_thread_access(thread, user_id)

    try:
        messages_raw = db.list_chat_messages(
            thread_id,
            limit=limit,
            ascending=True,
        )
    except TransientDatabaseError as exc:
        _raise_transient_chat_error(exc)
    messages = [_convert_message(record) for record in messages_raw]
    last_message = messages[-1] if messages else None
    summary = _convert_summary(thread, last_message)
    return ChatThread(
        id=summary.id,
        title=summary.title,
        kind=summary.kind,
        agent_keys=summary.agent_keys,
        last_message_preview=summary.last_message_preview,
        last_activity=summary.last_activity,
        unread_count=summary.unread_count,
        active_session_id=summary.active_session_id,
        messages=messages,
    )


@router.delete("/chats/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat_thread_endpoint(
    thread_id: str,
    context=Depends(get_database_with_user),
) -> Response:
    """Delete a chat thread owned by the authenticated user."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        thread = db.get_chat_thread(thread_id)
    except TransientDatabaseError as exc:
        _raise_transient_chat_error(exc)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    _ensure_thread_access(thread, user_id)

    if not db.delete_chat_thread(thread_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete chat thread",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/{thread_id}/clear", status_code=status.HTTP_204_NO_CONTENT)
def clear_chat_history_endpoint(
    thread_id: str,
    context=Depends(get_database_with_user),
) -> Response:
    """Clear all messages from a chat thread owned by the authenticated user."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        thread = db.get_chat_thread(thread_id)
    except TransientDatabaseError as exc:
        _raise_transient_chat_error(exc)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    _ensure_thread_access(thread, user_id)

    if not db.clear_chat_messages(thread_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear chat history",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/chats/{thread_id}/session/reset",
    response_model=ChatSessionResetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reset_chat_session(
    thread_id: str,
    context=Depends(get_database_with_user),
) -> ChatSessionResetResponse:
    """Start a new agent session for the given chat thread."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        thread = db.get_chat_thread(thread_id)
    except TransientDatabaseError as exc:
        _raise_transient_chat_error(exc)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found",
        )

    _ensure_thread_access(thread, user_id)

    new_session_id = uuid.uuid4().hex

    try:
        db.update_chat_thread(thread_id, {"active_session_id": new_session_id})
    except Exception as exc:
        print(f"Failed to update active session for thread {thread_id}: {exc}")

    timestamp = datetime.now(timezone.utc)
    timestamp_iso = timestamp.isoformat()
    friendly_time = timestamp.strftime("%b %d, %Y %H:%M UTC")
    content = f"New session started · {friendly_time}"
    payload = {
        "event": "session_reset",
        "session_id": new_session_id,
        "started_at": timestamp_iso,
    }

    message_record = db.insert_chat_message(
        thread_id=thread_id,
        role="system",
        content=content,
        author=None,
        payload=payload,
        organization_id=thread.get("organization_id"),
        user_id=user_id,
        session_id=new_session_id,
    )

    if not message_record:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record session reset",
        )

    db.touch_chat_thread(thread_id)

    message = _convert_message(message_record)
    notification = {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "author": message.author,
        "created_at": message.created_at.isoformat() if hasattr(message.created_at, "isoformat") else message.created_at,
        "attachments": [],
        "payload": payload,
    }
    try:
        await notify_new_message(user_id, thread_id, notification)
    except Exception as exc:
        print(f"Failed to broadcast session reset message: {exc}")

    return ChatSessionResetResponse(session_id=new_session_id, message=message)


@router.post(
    "/chats/{thread_id}/messages",
    response_model=ChatMessage,
    status_code=status.HTTP_201_CREATED,
)
async def send_chat_message(
    thread_id: str,
    payload: ChatMessageCreate,
    context=Depends(get_database_with_user),
) -> ChatMessage:
    """Append a user-authored message to a chat thread and trigger agent response."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        return await send_chat_message_internal(
            user_id, 
            thread_id, 
            payload.content.strip(),
            attachments=[attachment.dict() for attachment in payload.attachments],
            session_id=payload.session_id
        )
    except Exception as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


async def send_chat_message_internal(
    user_id: str,
    thread_id: str,
    content: str,
    *,
    attachments: Optional[Sequence[Dict[str, Any]]] = None,
    session_id: str = None,
):
    """Internal function for sending messages with session management (used by both HTTP and WebSocket)."""
    
    # Import here to avoid circular imports
    from app.api.dependencies import get_database_client
    from app.core.thread_manager import thread_manager
    from app.services.session_manager import session_manager
    
    db = get_database_client()
    if not db:
        raise Exception("Database client is not configured")

    try:
        user_context, organization, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise Exception(f"User not found: {exc}")

    try:
        thread = db.get_chat_thread(thread_id)
    except TransientDatabaseError as exc:
        _raise_transient_chat_error(exc)
    if not thread:
        raise Exception("Chat thread not found")
        
    _ensure_thread_access(thread, user_id)

    # ALWAYS ensure agent_keys are correct before processing - prevents recurring issues
    thread_manager.ensure_agent_keys(thread_id, user_context)
    
    # Re-fetch thread after potential agent_keys fix
    try:
        thread = db.get_chat_thread(thread_id)
    except TransientDatabaseError as exc:
        _raise_transient_chat_error(exc)

    # Determine eligible agents for this turn
    thread_agent_keys = _normalize_agent_keys(thread.get("agent_keys", []))
    enabled_agent_keys = set(user_context.enabled_agents or [])
    eligible_agent_keys = [key for key in thread_agent_keys if key in enabled_agent_keys]

    dispatcher_result: Optional[TeamDispatchResult] = None
    turn_agent_keys: List[str] = eligible_agent_keys.copy()

    if (thread.get("kind") or "").lower() == "group" and eligible_agent_keys:
        history_records = db.list_chat_messages(
            thread_id=thread_id,
            limit=12,
            ascending=False,
        )
        dispatcher_result = await team_chat_dispatcher.dispatch(
            message_text=content,
            enabled_agent_keys=eligible_agent_keys,
            thread_title=_coerce_thread_title(thread),
            messages=history_records,
        )

        if dispatcher_result.error:
            print(f"[dispatcher] Routing fallback due to error: {dispatcher_result.error}")

        if dispatcher_result.selected_agent_key:
            selected = dispatcher_result.selected_agent_key
            if selected in eligible_agent_keys:
                turn_agent_keys = [selected]
            else:
                print(
                    f"[dispatcher] Selected agent '{selected}' not in eligible roster {eligible_agent_keys}; "
                    "falling back to default routing."
                )
        elif dispatcher_result.declined:
            turn_agent_keys = []

    # Session management: Determine session for agent conversation continuity
    thread_active_session = thread.get("active_session_id")
    if thread_active_session is not None:
        try:
            thread_active_session = str(thread_active_session)
        except Exception:
            thread_active_session = None

    active_session_id = session_id or thread_active_session
    if turn_agent_keys:
        primary_agent_key = turn_agent_keys[0]
        active_session_id = session_manager.get_or_create_session(
            user_context=user_context,
            thread_id=thread_id,
            agent_key=primary_agent_key,
            provided_session_id=active_session_id,
        )

    if active_session_id and active_session_id != thread_active_session:
        try:
            db.update_chat_thread(thread_id, {"active_session_id": active_session_id})
        except Exception as exc:
            print(f"Failed to persist active_session_id for thread {thread_id}: {exc}")

    attachments_list: List[Dict[str, Any]] = []
    if attachments:
        for entry in attachments:
            try:
                attachment_model = ChatMessageAttachment.model_validate(entry)
            except ValidationError as exc:
                print(f"Invalid attachment skipped: {exc}")
                continue

            payload_dict = attachment_model.model_dump(exclude_none=True)

            uri = payload_dict.get("uri")
            if uri and not payload_dict.get("relative_path"):
                for marker in ("/v1/files/download/", "/api/v1/files/download/"):
                    if marker in uri:
                        payload_dict["relative_path"] = uri.split(marker, 1)[1].lstrip("/")
                        break

            attachments_list.append(payload_dict)

    if attachments_list:
        print(
            f"[API] Received {len(attachments_list)} attachment(s) for message {thread_id}: {attachments_list}"
        )

    # Store the user message
    message_payload: Dict[str, Any] = {}
    if active_session_id:
        message_payload["session_id"] = active_session_id
    if attachments_list:
        message_payload["attachments"] = attachments_list

    message_record = db.insert_chat_message(
        thread_id=thread_id,
        role="user",
        content=content,
        author=user_context.display_name or user_context.email or "User",
        payload=message_payload,
        organization_id=organization.get("id"),
        user_id=user_id,
        session_id=active_session_id,
    )
    if not message_record:
        raise Exception("Failed to record chat message")

    db.touch_chat_thread(thread_id)

    # Send WebSocket notification for the user message
    user_message_data = _convert_message(message_record)
    try:
        serialized_attachments = [attachment.dict(exclude_none=True) for attachment in user_message_data.attachments]
        notification_data = {
            "id": user_message_data.id,
            "role": user_message_data.role,
            "content": user_message_data.content,
            "author": user_message_data.author,
            "created_at": user_message_data.created_at,
            "attachments": serialized_attachments,
            "payload": message_payload,
        }
        if hasattr(notification_data["created_at"], "isoformat"):
            notification_data["created_at"] = notification_data["created_at"].isoformat()
            
        await notify_new_message(user_id, thread_id, notification_data)
    except Exception as ws_error:
        print(f"WebSocket notification failed: {ws_error}")

    # Handle scenarios where no teammate will be dispatched
    if not turn_agent_keys:
        await _emit_dispatcher_decline_message(
            db=db,
            thread_id=thread_id,
            organization_id=organization.get("id"),
            user_id=user_id,
        )
        try:
            from app.api.routes.websocket import notify_chat_status

            await notify_chat_status(user_id, thread_id, "agent_processing_completed")
        except Exception as ws_error:
            print(f"Failed to send completion status after dispatcher decline: {ws_error}")
        return user_message_data

    # Trigger agent processing - Mobile-first approach
    try:
        if turn_agent_keys:
            # Process agents directly without the web UI's ChatManager complexity
            from app.core.agent_runner import process_agents_for_message
            
            # First check if any agents will actually respond (without sending processing status yet)
            result = await process_agents_for_message(
                user_id=user_id,
                thread_id=thread_id,
                message_text=content,
                author_label=user_context.display_name or user_context.email or "User",
                agent_keys=turn_agent_keys,
                database_client=db,
                organization_id=organization.get("id"),
                session_id=active_session_id,
                attachments=attachments_list,
            )
            
            # Check if any agents actually responded or were dispatched
            agent_responded = result and "messages" in result and len(result["messages"]) > 1
            celery_agents_dispatched = result and result.get("celery_agents_dispatched", [])
            
            if agent_responded:
                # Notify that agent processing started (retrospectively, since we already processed)
                try:
                    from app.api.routes.websocket import notify_chat_status
                    await notify_chat_status(user_id, thread_id, "agent_processing_started")
                    print(f"Sent agent_processing_started notification for thread {thread_id}")
                except Exception as ws_error:
                    print(f"Failed to send agent_processing_started notification: {ws_error}")
            elif not celery_agents_dispatched:
                # No agents responded AND no Celery agents were dispatched, send completion immediately
                try:
                    from app.api.routes.websocket import notify_chat_status
                    await notify_chat_status(user_id, thread_id, "agent_processing_completed")
                    print(f"No agents responded for thread {thread_id}, sent completion status")
                except Exception as ws_error:
                    print(f"Failed to send agent_processing_completed notification: {ws_error}")
            else:
                # Celery agents were dispatched, they will handle their own completion
                print(f"Celery agents {celery_agents_dispatched} dispatched for thread {thread_id}, skipping immediate completion")
            
            # If an agent response was generated, notify via WebSocket
            if result and "messages" in result and len(result["messages"]) > 1:
                agent_message = result["messages"][-1]
                try:
                    print(f"Sending WebSocket notification for agent message: {agent_message.get('id')}")
                    raw_attachments = agent_message.get("attachments", []) or []
                    serialized_agent_attachments: List[Dict[str, Any]] = []
                    if isinstance(raw_attachments, list):
                        for entry in raw_attachments:
                            if isinstance(entry, dict):
                                try:
                                    attachment_model = ChatMessageAttachment.model_validate(entry)
                                    serialized_agent_attachments.append(attachment_model.model_dump(exclude_none=True))
                                except ValidationError as exc:
                                    print(f"Agent attachment validation failed: {exc}")
                            else:
                                print("Agent attachment entry ignored due to non-dict payload")
                    notification_data = {
                        "id": agent_message.get("id"),
                        "role": agent_message.get("role", "assistant"),
                        "content": agent_message.get("content", ""),
                        "author": agent_message.get("author", "Agent"),
                        "created_at": agent_message.get("timestamp") or agent_message.get("created_at"),
                        "attachments": serialized_agent_attachments,
                        "payload": agent_message.get("payload") or {},
                    }
                    if hasattr(notification_data["created_at"], "isoformat"):
                        notification_data["created_at"] = notification_data["created_at"].isoformat()
                    
                    await notify_new_message(user_id, thread_id, notification_data)
                except Exception as ws_error:
                    print(f"WebSocket notification for agent message failed: {ws_error}")
                    
    except Exception as exc:
        print(f"Agent processing failed for thread {thread_id}: {exc}")

    return user_message_data
