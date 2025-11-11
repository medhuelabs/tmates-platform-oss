"""Agent job management endpoints for the public API."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.billing import BillingManager
from app.core import resolve_user_context
from app.db import DatabaseClient
from app.worker.tasks import run_agent_job
from app.worker.celery_app import celery_app

from ..dependencies import get_current_user_id, get_database
from ..schemas import AgentJob, AgentJobCreate


router = APIRouter()


def _int_from_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_ACTIVE_JOBS_PER_USER = _int_from_env("AGENT_MAX_ACTIVE_JOBS_PER_USER", 3)
DEFAULT_AGENT_QUEUE = os.getenv("CELERY_AGENT_QUEUE", "agents")
ACTIVE_STATUSES = {"queued", "running"}


def _resolve_execution_plan(
    agent_key: str,
    task: Optional[str],
    cli_args: Dict[str, Any],
    env_overrides: Dict[str, str],
) -> Optional[str]:
    """Translate the requested task into a canonical task label for agents that expect it."""

    normalized_agent = (agent_key or "").strip().casefold()
    normalized_task = (task or "").strip().casefold()

    # Default behaviour for standard agents is a single CLI-style run.
    canonical_task = normalized_task or None
    if canonical_task:
        cli_args.setdefault("task", canonical_task)
    return canonical_task


@router.post(
    "/jobs",
    response_model=AgentJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_agent_job(
    request: AgentJobCreate,
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> AgentJob:
    """Queue an agent execution request for the authenticated user."""

    try:
        user_context, organization, enabled_agents = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to load user context") from exc

    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    requested_key = (request.agent_key or "").strip()
    if not requested_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="agent_key is required")

    enabled_keys = {key.casefold() for key in enabled_agents}
    if requested_key.casefold() not in enabled_keys:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Agent '{requested_key}' is not enabled for this user")

    if MAX_ACTIVE_JOBS_PER_USER > 0:
        existing_jobs = db.list_agent_jobs(user_id, limit=MAX_ACTIVE_JOBS_PER_USER * 4)
        active_jobs = [row for row in existing_jobs if str(row.get("status", "")).lower() in ACTIVE_STATUSES]
        if len(active_jobs) >= MAX_ACTIVE_JOBS_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Active job limit reached ({MAX_ACTIVE_JOBS_PER_USER}). Wait for existing jobs to finish before queuing more.",
            )

    billing_manager = BillingManager(db)
    plan_context = getattr(user_context, "plan_context", None)
    if billing_manager.enabled:
        if plan_context is None:
            try:
                plan_context = billing_manager.get_plan_context(
                    organization["id"],
                    active_agents=len(enabled_agents),
                )
            except Exception as plan_exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to resolve billing plan") from plan_exc
        quota_error = billing_manager.job_quota_error(plan_context)
        if quota_error:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=quota_error)

    cli_args = {str(key): value for key, value in (request.cli_args or {}).items()}
    env_overrides = {str(key): str(value) for key, value in (request.env_overrides or {}).items()}
    extra_args = [str(arg) for arg in (request.extra_args or [])]
    metadata = dict(request.metadata or {})
    task = request.task

    canonical_task = _resolve_execution_plan(requested_key, task, cli_args, env_overrides)
    payload = {
        "task": canonical_task,
        "cli_args": cli_args,
        "env_overrides": env_overrides,
        "extra_args": extra_args,
    }

    job_record = db.create_agent_job(user_id, requested_key, payload=payload, metadata=metadata)

    queue_name = str(metadata.get("queue") or DEFAULT_AGENT_QUEUE)

    try:
        run_agent_job.apply_async(
            args=[job_record["id"], user_id, requested_key, payload],
            queue=queue_name,
        )
    except Exception as exc:
        error_payload = {
            "message": "Failed to enqueue Celery task",
            "details": str(exc),
        }
        db.update_agent_job(job_record["id"], status="failed", error=error_payload, finished_at=datetime.now(timezone.utc))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unable to enqueue job") from exc

    return AgentJob.from_record(job_record)


@router.get("/jobs", response_model=List[AgentJob], status_code=status.HTTP_200_OK)
def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> List[AgentJob]:
    jobs = db.list_agent_jobs(user_id, limit=limit)
    return [AgentJob.from_record(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=AgentJob, status_code=status.HTTP_200_OK)
def get_job(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
) -> AgentJob:
    job = db.get_agent_job(job_id, auth_user_id=user_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return AgentJob.from_record(job)


@router.post("/jobs/{job_id}/cancel", response_model=AgentJob, status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
    db: DatabaseClient = Depends(get_database),
):
    job = db.get_agent_job(job_id, auth_user_id=user_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    status_value = str(job.get("status", "")).lower()
    if status_value not in ACTIVE_STATUSES.union({"cancelling"}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job is not currently running")

    metadata = dict(job.get("metadata") or {})
    metadata["cancel_requested_at"] = datetime.now(timezone.utc).isoformat()
    metadata["cancel_requested_by"] = user_id

    updated_job = db.update_agent_job(
        job_id,
        status="cancelled",
        finished_at=datetime.now(timezone.utc),
        progress=1.0,
        metadata=metadata,
    )

    try:
        celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")
    except Exception as exc:
        print(f"Failed to revoke job {job_id}: {exc}")

    thread_id = metadata.get("thread_id")
    if thread_id:
        from app.api.routes.websocket import notify_chat_status

        try:
            await notify_chat_status(user_id, thread_id, "agent_cancelled", {"agent": job.get("agent_key")})
            await notify_chat_status(user_id, thread_id, "agent_processing_completed")
        except Exception as ws_error:
            print(f"Failed to send cancellation status for job {job_id}: {ws_error}")

    record = updated_job or job
    return AgentJob.from_record(record)
