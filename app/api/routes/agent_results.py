"""Internal API endpoint for Celery workers to post agent results or status updates."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ValidationError

from app.db import get_database_client
from app.core.agent_runner import resolve_user_context, apply_user_context_to_env
from app.api.routes.websocket import notify_chat_status
from app.api.routes import chats as chats_routes
from app.api.schemas import ChatMessageAttachment

router = APIRouter()


def _strip_attachment_links(text: str, attachments: List[Dict[str, Any]]) -> str:
    if not text or not attachments:
        return text

    sanitized = text
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        candidates: list[str] = []
        for key in ("download_url", "uri"):
            value = attachment.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)

        rel = attachment.get("relative_path")
        if isinstance(rel, str) and rel.strip():
            rel_clean = rel.strip()
            candidates.extend(
                [
                    f"/files/download/{rel_clean}",
                    f"/v1/files/download/{rel_clean}",
                    f"/api/v1/files/download/{rel_clean}",
                ]
            )

        for candidate in sorted({c for c in candidates if c}, key=len, reverse=True):
            if candidate not in sanitized:
                continue
            pattern = rf"\s*[:\-]?\s*{re.escape(candidate)}"
            sanitized = re.sub(pattern, "", sanitized)

    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized).strip()
    sanitized = re.sub(
        r"(?:\s*[-:])?\s*(download here|download|view attachments?|view)\.?$",
        "",
        sanitized,
        flags=re.IGNORECASE,
    ).strip()

    cleaned_lower = sanitized.lower().rstrip(".:!")
    if cleaned_lower in {"", "download", "download here", "view", "view attachment", "view attachments"}:
        sanitized = ""

    if not sanitized and attachments:
        valid_count = sum(1 for item in attachments if isinstance(item, dict))
        noun = "file" if valid_count == 1 else "files"
        sanitized = f"Here you go. I attached the {noun} for you."

    return sanitized


class AgentResultPayload(BaseModel):
    """Payload for agent result submission."""
    job_id: str
    agent_key: str
    user_id: str
    result_data: str  # The actual response text from the agent
    task_type: str = "chat"  # Default to chat for now
    metadata: Dict[str, Any] = {}  # Extra context like thread_id, etc.
    intermediate: bool = False
    attachments: Optional[list[Dict[str, Any]]] = None


class AgentStatusPayload(BaseModel):
    """Payload for agent status heartbeats sent from background workers."""

    job_id: Optional[str] = None
    agent_key: str
    user_id: str
    thread_id: str
    status: str = "agent_typing"
    stage: Optional[str] = None
    status_message: Optional[str] = None
    progress: Optional[float] = None
    extra: Dict[str, Any] = {}


class ChatHistoryRequest(BaseModel):
    job_id: Optional[str] = None
    agent_key: str
    user_id: str
    thread_id: str
    limit: int = 10


@router.post("/internal/agent-result", status_code=status.HTTP_200_OK)
async def handle_agent_result(payload: AgentResultPayload):
    """
    Internal endpoint for Celery workers to submit agent results.
    Routes the result to appropriate handlers based on task_type.
    """
    try:
        # Get database client
        db = get_database_client()
        
        # Resolve user context for WebSocket notifications
        user_context, organization, _ = resolve_user_context(payload.user_id)
        apply_user_context_to_env(user_context)
        
        # Route based on task type
        if payload.task_type == "chat":
            return await _handle_chat_result(db, payload, user_context, organization)
        else:
            # For future task types: image_generation, document_creation, etc.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported task_type: {payload.task_type}"
            )
            
    except Exception as exc:
        print(f"Error handling agent result for job {payload.job_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process agent result: {str(exc)}"
        )


@router.post("/internal/chat-history", status_code=status.HTTP_200_OK)
async def read_chat_history(payload: ChatHistoryRequest):
    db = get_database_client()

    try:
        user_context, _, _ = resolve_user_context(payload.user_id)
        apply_user_context_to_env(user_context)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    limit = max(1, min(payload.limit, 50))

    try:
        records = db.list_chat_messages(
            payload.thread_id,
            limit=limit,
            ascending=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load chat history: {exc}",
        ) from exc

    history: List[Dict[str, Any]] = []
    for record in records or []:
        payload_block = record.get("payload") or {}
        attachments_list: List[Dict[str, Any]] = []
        attachments_raw = payload_block.get("attachments") or record.get("attachments") or []
        if isinstance(attachments_raw, list):
            for entry in attachments_raw:
                if not isinstance(entry, dict):
                    continue
                attachment = chats_routes._convert_attachment(entry)
                if attachment:
                    attachments_list.append(attachment.model_dump(exclude_none=True))

        message_entry = {
            "id": str(record.get("id")),
            "role": str(record.get("role") or "assistant"),
            "author": record.get("author"),
            "content": record.get("content"),
            "created_at": record.get("created_at"),
            "attachments": attachments_list,
        }
        created_at = message_entry.get("created_at")
        if hasattr(created_at, "isoformat"):
            message_entry["created_at"] = created_at.isoformat()

        history.append(message_entry)

    history.reverse()

    return {
        "thread_id": payload.thread_id,
        "messages": history,
    }


async def _handle_chat_result(db, payload: AgentResultPayload, user_context, organization):
    """Handle chat task results - save to database and send WebSocket notification."""
    
    # Extract chat-specific metadata
    thread_id = payload.metadata.get("thread_id")
    if not thread_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="thread_id required for chat tasks"
        )
    
    attachments_list: List[Dict[str, Any]] = []
    if payload.attachments:
        for entry in payload.attachments:
            if not isinstance(entry, dict):
                continue
            try:
                attachment_model = ChatMessageAttachment.model_validate(entry)
            except ValidationError as exc:
                print(f"Invalid agent attachment skipped: {exc}")
                continue

            payload_dict = attachment_model.model_dump(exclude_none=True)
            uri = payload_dict.get("uri")
            if uri and not payload_dict.get("relative_path"):
                for marker in ("/v1/files/download/", "/api/v1/files/download/"):
                    if marker in uri:
                        payload_dict["relative_path"] = uri.split(marker, 1)[1].lstrip("/")
                        break

            attachments_list.append(payload_dict)

    # Save the agent response as a chat message
    message_payload: Dict[str, Any] = {}
    session_id = None
    if isinstance(payload.metadata, dict):
        metadata_session = payload.metadata.get("session_id")
        if isinstance(metadata_session, str) and metadata_session.strip():
            session_id = metadata_session.strip()
            message_payload["session_id"] = session_id
    if payload.metadata.get("parameters_text"):
        message_payload["parameters_text"] = payload.metadata.get("parameters_text")
    if attachments_list:
        message_payload["attachments"] = attachments_list

    sanitized_content = _strip_attachment_links(payload.result_data, attachments_list)

    message_record = db.insert_chat_message(
        thread_id=thread_id,
        role="assistant",
        content=sanitized_content,
        author=payload.agent_key.title(),
        payload=message_payload,
        organization_id=organization.get("id") if organization else None,
        user_id=user_context.user_id,
        session_id=session_id,
    )
    
    if message_record:
        # Send WebSocket notification for the agent response
        from app.api.routes.websocket import notify_new_message
        
        agent_message_data = {
            "id": str(message_record.get("id")),
            "role": "assistant", 
            "content": sanitized_content,
            "author": payload.agent_key.title(),
            "created_at": message_record.get("created_at"),
            "attachments": attachments_list,
            "payload": message_payload,
        }
        
        # Format datetime if needed
        if hasattr(agent_message_data["created_at"], "isoformat"):
            agent_message_data["created_at"] = agent_message_data["created_at"].isoformat()
            
        await notify_new_message(user_context.user_id, thread_id, agent_message_data)
        print(f"Saved and sent agent response from {payload.agent_key}: {payload.result_data[:100]}...")
        
        # Send agent processing completed notification for Celery agents
        from app.api.routes.websocket import notify_chat_status
        next_status = payload.metadata.get("next_status")
        if next_status and isinstance(next_status, dict):
            status_name = next_status.get("status")
            status_data = next_status.get("data") or {}
            if status_name:
                try:
                    await notify_chat_status(user_context.user_id, thread_id, status_name, status_data)
                    print(f"Sent follow-up status '{status_name}' for agent {payload.agent_key}")
                except Exception as ws_error:
                    print(f"Failed to send follow-up status for agent {payload.agent_key}: {ws_error}")

        if not payload.intermediate:
            try:
                await notify_chat_status(user_context.user_id, thread_id, "agent_processing_completed")
                print(f"Sent agent_processing_completed notification for Celery agent {payload.agent_key}")
            except Exception as ws_error:
                print(f"Failed to send agent_processing_completed notification: {ws_error}")
        
        return {
            "status": "success",
            "message_id": message_record.get("id"),
            "job_id": payload.job_id
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save chat message to database"
        )


@router.post("/internal/chat-status", status_code=status.HTTP_202_ACCEPTED)
async def post_chat_status(payload: AgentStatusPayload):
    """
    Internal endpoint for Celery workers to push chat status heartbeats.
    This keeps client typing indicators alive without extending fixed timeouts.
    """

    data: Dict[str, Any] = {"agent": payload.agent_key}
    if payload.stage:
        data["stage"] = payload.stage
    if payload.status_message:
        data["status_message"] = payload.status_message
    if payload.progress is not None:
        data["progress"] = payload.progress
    if payload.extra:
        data.update(payload.extra)

    db = get_database_client()

    if payload.job_id:
        try:
            job_record = db.get_agent_job(payload.job_id)
        except Exception as exc:
            print(f"Failed to load job {payload.job_id} for status update: {exc}")
            job_record = None

        metadata: Dict[str, Any] | None = None

        if job_record:
            metadata = dict(job_record.get("metadata") or {})
            if payload.stage:
                metadata["last_stage"] = payload.stage
            if payload.status_message:
                metadata["last_status_message"] = payload.status_message
            metadata["last_status_at"] = datetime.now(timezone.utc).isoformat()

        try:
            db.update_agent_job(
                payload.job_id,
                status="running",
                progress=payload.progress,
                metadata=metadata,
            )
        except Exception as exc:
            print(f"Failed to update job {payload.job_id} status heartbeat: {exc}")

    try:
        await notify_chat_status(payload.user_id, payload.thread_id, payload.status, data)
    except Exception as exc:
        print(f"Failed to deliver chat status heartbeat for thread {payload.thread_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deliver chat status: {exc}"
        ) from exc

    return {"status": "accepted"}
