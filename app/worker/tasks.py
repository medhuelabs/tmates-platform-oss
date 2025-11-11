"""Celery task definitions for agent execution.

IMPORTANT: WebSocket Notification Architecture
==============================================
This file processes agents in background worker processes. Workers CANNOT send
WebSocket notifications directly because WebSocket connections are stored in the
API process memory.

CORRECT PATTERN: Use _post_chat_result_to_api() to call the internal API endpoint,
which then sends WebSocket notifications from the API process that owns the connections.

See docs/websocket-architecture.md for full details.
"""

from __future__ import annotations

import re
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from celery.exceptions import MaxRetriesExceededError
from celery.utils.log import get_task_logger
from sqlalchemy.exc import DBAPIError

from app.billing import BillingManager
from app.core import (
    apply_user_context_to_env,
    resolve_user_context,
)
from app.core.api_urls import build_api_url
from app.db import get_database_client

from .celery_app import celery_app


logger = get_task_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _strip_attachment_links(text: str, attachments: Optional[list[Dict[str, Any]]]) -> str:
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

    if attachments:
        cleaned_lower = sanitized.lower().rstrip(".:!")
        if cleaned_lower in {"", "download", "download here", "view", "view attachment", "view attachments"}:
            sanitized = ""

    if not sanitized and attachments:
        valid_count = sum(1 for item in attachments if isinstance(item, dict))
        noun = "file" if valid_count == 1 else "files"
        sanitized = f"Here you go. I attached the {noun} for you."

    return sanitized


def _post_chat_result_to_api(
    job_id: str,
    agent_key: str,
    user_id: str,
    result_data: str,
    thread_id: str,
    attachments: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Post chat result to the API endpoint for processing."""
    try:
        payload = {
            "job_id": job_id,
            "agent_key": agent_key,
            "user_id": user_id,
            "result_data": result_data,
            "task_type": "chat",
            "metadata": {
                "thread_id": thread_id
            },
        }
        if attachments:
            payload["attachments"] = attachments

        response = requests.post(
            build_api_url("v1", "internal", "agent-result"),
            json=payload,
            timeout=30,
        )
        
        if response.status_code != 200:
            raise RuntimeError(
                f"API error {response.status_code}: {response.text}",
            )

        return response.json()

    except Exception as exc:
        logger.exception("Failed to post agent result for job %s", job_id)
        raise


def _post_chat_status_to_api(
    *,
    job_id: Optional[str],
    agent_key: str,
    user_id: str,
    thread_id: Optional[str],
    status: str,
    stage: Optional[str] = None,
    status_message: Optional[str] = None,
    progress: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Send chat status updates back through the API for WebSocket fan-out."""
    if not thread_id:
        return

    payload: Dict[str, Any] = {
        "job_id": job_id,
        "agent_key": agent_key,
        "user_id": user_id,
        "thread_id": thread_id,
        "status": status,
    }
    if stage:
        payload["stage"] = stage
    if status_message:
        payload["status_message"] = status_message
    if progress is not None:
        payload["progress"] = progress
    if extra:
        payload["extra"] = extra

    try:
        response = requests.post(
            build_api_url("v1", "internal", "chat-status"),
            json=payload,
            timeout=15,
        )
        if response.status_code >= 400:
            logger.warning(
                "Chat status relay failed (%s) for job %s: %s",
                response.status_code,
                job_id,
                response.text,
            )
    except Exception as exc:
        logger.warning("Unable to relay chat status for job %s: %s", job_id, exc)


TRANSIENT_DB_MARKERS: List[str] = [
    "connection was closed",
    "connection does not exist",
    "server closed the connection",
    "terminating connection due to administrator command",
    "connection already closed",
]


def _matches_transient_db_markers(payload: str) -> bool:
    if not payload:
        return False
    combined = payload.lower()
    return any(marker in combined for marker in TRANSIENT_DB_MARKERS)


def _is_transient_db_error(exc: Exception) -> bool:
    """Best-effort detection for connection hiccups that are worth retrying."""
    candidates: List[str] = []
    if isinstance(exc, DBAPIError):
        candidates.append(str(exc))
        if exc.orig and exc.orig is not exc:
            candidates.append(str(exc.orig))
    else:
        candidates.append(str(exc))

    return _matches_transient_db_markers(" ".join(candidates))


def _agent_result_indicates_transient_db_error(result: Dict[str, Any]) -> bool:
    """Detect when an agent API response is bubbling up a transient DB outage."""
    error_value = result.get("error")
    candidates: List[str] = []

    if isinstance(error_value, dict):
        for key in ("message", "detail", "error"):
            value = error_value.get(key)
            if isinstance(value, str):
                candidates.append(value)
        nested_type = error_value.get("error_type")
        if isinstance(nested_type, str):
            candidates.append(nested_type)
    elif error_value:
        candidates.append(str(error_value))

    error_type = result.get("error_type")
    if isinstance(error_type, str):
        candidates.append(error_type)

    return _matches_transient_db_markers(" ".join(candidates))


def _job_is_cancelled(db, job_id: str) -> bool:
    job_record = db.get_agent_job(job_id)
    status = str((job_record or {}).get("status", "")).lower()
    return status in {"cancelled", "cancelling"}


def _thread_id_from_payload(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not payload:
        return None
    thread_id = payload.get("thread_id")
    if thread_id:
        return thread_id
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get("thread_id")
    return None


@celery_app.task(bind=True, name="agent.run", max_retries=3)
def run_agent_job(
    self,
    job_id: str,
    auth_user_id: str,
    agent_key: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute an agent for the supplied user context."""

    db = get_database_client()
    payload = dict(payload or {})

    cli_args = payload.get("cli_args") or {}
    attachments_preview = cli_args.get("attachments") or payload.get("attachments")
    if not attachments_preview and isinstance(payload.get("metadata"), dict):
        attachments_preview = payload["metadata"].get("attachments")
    try:
        attachment_names = [
            str(item.get("name") or item.get("filename") or "")
            for item in (attachments_preview or [])
            if isinstance(item, dict)
        ]
        logger.info(
            "Starting agent job",
            extra={
                "job_id": job_id,
                "agent_key": agent_key,
                "attachments_count": len(attachments_preview or []),
                "attachment_names": attachment_names,
            },
        )
    except Exception:
        logger.info("Starting agent job (attachments summary failed)", extra={"job_id": job_id, "agent_key": agent_key})

    if _job_is_cancelled(db, job_id):
        logger.info("Job %s was cancelled before start", job_id)
        return {"job_id": job_id, "status": "cancelled"}

    db.update_agent_job(job_id, status="running", started_at=_utcnow(), progress=0.05)

    organization = None
    billing_manager: Optional[BillingManager] = None
    try:
        user_context, organization, enabled_agents = resolve_user_context(auth_user_id)
        apply_user_context_to_env(user_context)
        billing_manager = BillingManager(db)
    except Exception as exc:  # pragma: no cover - defensive guard
        err_payload = {
            "message": "Failed to resolve user context",
            "details": str(exc),
        }
        logger.exception("Job %s failed while resolving user context", job_id)
        db.update_agent_job(job_id, status="failed", error=err_payload, finished_at=_utcnow(), progress=1.0)
        thread_id = _thread_id_from_payload(payload)
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_error",
            status_message=err_payload["message"],
        )
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_completed",
        )
        raise

    if agent_key.casefold() not in {key.casefold() for key in enabled_agents}:
        error_payload = {
            "message": f"Agent '{agent_key}' is not enabled for this user",
        }
        db.update_agent_job(job_id, status="failed", error=error_payload, finished_at=_utcnow(), progress=1.0)
        thread_id = _thread_id_from_payload(payload)
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_error",
            status_message=error_payload["message"],
        )
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_completed",
        )
        return {"job_id": job_id, "status": "failed", "error": error_payload}

    try:
        # Import agent creation function
        from app.registry.agents.loader import create_agent
        agent = create_agent(agent_key, user_context=user_context)

        if not hasattr(agent, "run_api"):
            raise NotImplementedError(
                f"Agent '{agent_key}' does not implement run_api; Celery workers require run_api support."
            )

        metadata = dict(payload.get("metadata", {}))
        metadata.setdefault("job_id", job_id)
        if payload.get("thread_id"):
            metadata.setdefault("thread_id", payload.get("thread_id"))
        payload["metadata"] = metadata
        api_request = {
            "message": cli_args.get("message"),
            "author": cli_args.get("author"),
            "thread_id": payload.get("thread_id"),
            "session_id": payload.get("session_id"),  # Pass session_id for conversation continuity
            "metadata": metadata,
            "attachments": metadata.get("attachments"),
        }

        if _job_is_cancelled(db, job_id):
            logger.info("Job %s cancellation detected before execution", job_id)
            db.update_agent_job(job_id, status="cancelled", finished_at=_utcnow(), progress=1.0)
            return {"job_id": job_id, "status": "cancelled"}

        result = agent.run_api(api_request)

        if _job_is_cancelled(db, job_id):
            logger.info("Job %s cancelled during execution", job_id)
            db.update_agent_job(job_id, status="cancelled", finished_at=_utcnow(), progress=1.0)
            return {"job_id": job_id, "status": "cancelled"}

        attachments: Optional[list[Dict[str, Any]]] = None
        agent_response = ""
        sanitized_response = ""

        if result.get("success", True):
            agent_response = result.get("response", "") or ""
            raw_attachments = result.get("attachments")
            if isinstance(raw_attachments, list):
                attachments = [item for item in raw_attachments if isinstance(item, dict)]
            else:
                attachments = None

            if attachments:
                print(f"[Celery] Agent {agent_key} returned attachments: {attachments}")

            sanitized_response = _strip_attachment_links(
                agent_response,
                attachments,
            )
            result_payload = {
                "exit_code": 0,
                "response": sanitized_response,
                "metadata": result.get("metadata", {}),
            }
            if attachments:
                result_payload["attachments"] = attachments
            status_value = "succeeded"
        else:
            if _agent_result_indicates_transient_db_error(result):
                error_value = result.get("error")
                if isinstance(error_value, dict):
                    message_detail = (
                        error_value.get("message")
                        or error_value.get("detail")
                        or error_value.get("error")
                    )
                else:
                    message_detail = error_value
                raise RuntimeError(
                    (message_detail if isinstance(message_detail, str) else None)
                    or "Transient database error reported by agent runtime"
                )

            error_payload = {
                "message": result.get("error", "Agent returned error"),
                "error_type": result.get("error_type", "UnknownError"),
            }
            if _job_is_cancelled(db, job_id):
                db.update_agent_job(job_id, status="cancelled", finished_at=_utcnow(), progress=1.0)
                return {"job_id": job_id, "status": "cancelled"}

            db.update_agent_job(job_id, status="failed", error=error_payload, finished_at=_utcnow(), progress=1.0)

            return {"job_id": job_id, "status": "failed", "error": error_payload}

        # Send agent response to API endpoint for saving and WebSocket notification
        thread_id = payload.get("thread_id") if payload else None
        if thread_id and (agent_response or attachments):
            try:
                # Use the existing internal API endpoint to handle message saving and WebSocket notifications
                _post_chat_result_to_api(
                    job_id=job_id,
                    agent_key=agent_key,
                    user_id=auth_user_id,
                    result_data=sanitized_response,
                    thread_id=thread_id,
                    attachments=attachments,
                )
            except Exception:
                logger.exception(
                    "Failed to forward agent result for job %s (thread %s)",
                    job_id,
                    thread_id,
                )
        
        if _job_is_cancelled(db, job_id):
            db.update_agent_job(job_id, status="cancelled", finished_at=_utcnow(), progress=1.0)
            return {"job_id": job_id, "status": "cancelled"}

        db.update_agent_job(
            job_id,
            status=status_value,
            result=result_payload if status_value == "succeeded" else None,
            error=None if status_value == "succeeded" else {"message": "Agent processing failed"},
            finished_at=_utcnow(),
            progress=1.0,
        )
        if status_value == "succeeded" and billing_manager and billing_manager.enabled and organization:
            try:
                usage_metadata: Dict[str, Any] = {
                    "job_id": job_id,
                    "agent_key": agent_key,
                }
                result_meta = result_payload.get("metadata") if isinstance(result_payload, dict) else None
                if isinstance(result_meta, dict):
                    tokens_used = result_meta.get("tokens_used") or result_meta.get("token_usage")
                    if tokens_used:
                        usage_metadata["tokens_used"] = tokens_used
                billing_manager.record_usage(
                    organization_id=organization["id"],
                    user_id=auth_user_id,
                    event_type="agent_job",
                    quantity=1,
                    metadata=usage_metadata,
                )
            except Exception:
                logger.exception("Failed to record usage for job %s", job_id)

        return {"job_id": job_id, "status": status_value}
    except MaxRetriesExceededError:
        logger.exception("Job %s exceeded retry limit", job_id)
        thread_id = _thread_id_from_payload(payload)
        error_payload = {
            "message": "Agent retries exhausted after transient database errors",
        }
        db.update_agent_job(job_id, status="failed", error=error_payload, finished_at=_utcnow(), progress=1.0)
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_error",
            status_message=error_payload["message"],
        )
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_completed",
        )
        raise
    except Exception as exc:  # pragma: no cover - guard against unhandled agent failures
        if _is_transient_db_error(exc):
            retry_count = getattr(self.request, "retries", 0)
            delay = min(60, 5 * (2 ** retry_count))
            logger.warning(
                "Transient DB error for job %s (attempt %s), retrying in %ss: %s",
                job_id,
                retry_count + 1,
                delay,
                exc,
            )
            # Preserve the job as running so the UI keeps the typing indicator alive.
            metadata = dict(payload.get("metadata") or {})
            metadata["last_transient_error"] = str(exc)
            payload["metadata"] = metadata
            try:
                db.update_agent_job(
                    job_id,
                    status="running",
                    metadata=metadata,
                    progress=0.1,
                )
            except Exception as update_error:
                logger.warning("Failed to record retry metadata for job %s: %s", job_id, update_error)

            raise self.retry(exc=exc, countdown=delay)

        error_payload = {
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        logger.exception("Job %s crashed", job_id)
        db.update_agent_job(job_id, status="failed", error=error_payload, finished_at=_utcnow(), progress=1.0)
        thread_id = _thread_id_from_payload(payload)
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_error",
            status_message=error_payload["message"],
        )
        _post_chat_status_to_api(
            job_id=job_id,
            agent_key=agent_key,
            user_id=auth_user_id,
            thread_id=thread_id,
            status="agent_processing_completed",
        )

        raise


__all__ = ["run_agent_job"]
