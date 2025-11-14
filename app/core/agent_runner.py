"""
Shared helpers for executing agent workers with a user context.

These utilities were originally implemented inside ``run.py`` but are now
centralised so the CLI, background workers, and web/API services can all
invoke agents through the same pathway.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
import logging
from typing import Dict, List, Optional, Sequence

from app.auth import (
    UserContext,
    get_default_user_context,
    load_user_context_from_env,
)
from app.billing import BillingManager
from app.config import load_envs
from app.db import get_database_client
from app.registry.agents.loader import create_agent
from app.logger import log

logger = logging.getLogger(__name__)


_CANCEL_KEYWORDS = ("stop", "cancel")


def _parse_cancel_command(message: str) -> tuple[bool, Optional[str]]:
    """Detect whether the user message is requesting a cancellation."""
    if not message:
        return False, None

    normalised = message.strip().casefold()
    if not normalised:
        return False, None

    for keyword in _CANCEL_KEYWORDS:
        if normalised == keyword:
            return True, None
        if normalised.startswith(f"{keyword} "):
            target = normalised[len(keyword) :].strip()
            return True, target or None

    return False, None


def apply_user_context_to_env(user_context: UserContext) -> None:
    """Expose user context details to subprocess consumers via environment."""

    os.environ["USER_CONTEXT_USER_ID"] = user_context.user_id or ""
    os.environ["USER_CONTEXT_DISPLAY_NAME"] = user_context.display_name or ""

    os.environ["USER_ID"] = user_context.user_id or ""
    os.environ["USER_DISPLAY_NAME"] = user_context.display_name or ""
    os.environ["USER_EMAIL"] = user_context.email or ""
    os.environ["USER_TIMEZONE"] = user_context.timezone or "UTC"

    os.environ["ENABLED_AGENTS"] = json.dumps(user_context.enabled_agents or [])
    os.environ["AGENT_CONFIGS"] = json.dumps(user_context.agent_configs or {})


def resolve_user_context(user_id: str) -> tuple[UserContext, dict, list[str]]:
    """Load user context and enabled agents for the given user ID."""

    db = get_database_client()
    org = db.get_user_organization(user_id)
    if not org:
        raise LookupError(f"No organization found for user ID: {user_id}")

    user_context = db.get_user_context(user_id)
    if user_context is None:
        raise LookupError(f"No user profile found for user ID: {user_id}")

    org_agents = db.get_organization_agents(org["id"])
    enabled_agents = [agent["key"] for agent in org_agents if agent.get("key")]

    # Filter out agents that no longer exist in the agent store
    try:
        from app.registry.agents.store import AgentStore
        agent_store = AgentStore()
        available_agents = set(agent_store.discover_agents().keys())
        enabled_agents = [key for key in enabled_agents if key in available_agents]
    except Exception as e:
        logger.warning("Could not filter agents by availability: %s", e)

    billing_manager = BillingManager(db)
    try:
        plan_context = billing_manager.get_plan_context(org["id"], active_agents=len(enabled_agents))
        user_context.plan_context = plan_context
    except Exception as plan_exc:
        logger.warning(
            "Failed to resolve billing plan for org %s: %s",
            org.get("id") if org else None,
            plan_exc,
        )

    user_context.enabled_agents = enabled_agents
    return user_context, org, enabled_agents


def run_worker(
    agent_key: str,
    *,
    cli_args: Dict[str, object] | None = None,
    env_overrides: dict[str, str] | None = None,
    user_context: UserContext | None = None,
    extra_args: List[str] | None = None,
) -> int:
    """Load configuration and execute the specified agent worker."""

    project_root = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(project_root, "..", ".."))

    load_envs(project_root, agent_key)
    if env_overrides:
        for key, value in env_overrides.items():
            os.environ[key] = value

    # Load user context if not provided
    if user_context is None:
        user_context = load_user_context_from_env()
        if user_context is None:
            user_context = get_default_user_context()
            log("[dispatcher] using default user context for backwards compatibility")

    if user_context is not None:
        apply_user_context_to_env(user_context)

    # Check if user has this agent enabled
    if not user_context.is_agent_enabled(agent_key):
        log(f"[dispatcher] agent '{agent_key}' is disabled for user {user_context.user_id}")
        return 0

    log(f"[dispatcher] starting worker '{agent_key}' for user {user_context.user_id}")
    agent = create_agent(agent_key, user_context=user_context)
    try:
        return agent.run(
            cli_args=cli_args or {},
            extra_args=extra_args or [],
        )
    except NotImplementedError as exc:
        logger.error("Agent '%s' does not implement run(): %s", agent_key, exc)
        return 1


async def process_agents_for_message(
    user_id: str,
    thread_id: str,
    message_text: str,
    author_label: str,
    agent_keys: List[str],
    database_client,
    organization_id: str,
    session_id: str = None,
    attachments: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, object]:
    """
    Mobile-first agent processing - directly invoke agents for a message without 
    the web UI's thread management complexity.
    """
    from app.api.routes.websocket import notify_new_message, notify_chat_status
    from app.worker.celery_app import celery_app
    from app.worker.tasks import run_agent_job
    from datetime import datetime, timezone
    
    normalised_attachments: List[Dict[str, Any]] = []
    if attachments:
        for entry in attachments:
            if isinstance(entry, dict):
                normalised_attachments.append(dict(entry))
    if normalised_attachments:
        attachment_names = [
            str(item.get("name") or item.get("filename") or item.get("relative_path") or item.get("uri") or "")
            for item in normalised_attachments
        ]
        logger.info(
            "process_agents_for_message forwarding %s attachment(s): %s",
            len(normalised_attachments),
            attachment_names,
            extra={
                "thread_id": thread_id,
                "attachments_count": len(normalised_attachments),
            },
        )
        logger.debug(
            "process_agents_for_message forwarding %s attachment(s): %s",
            len(normalised_attachments),
            attachment_names,
        )

    try:
        user_context, organization, _ = resolve_user_context(user_id)
        apply_user_context_to_env(user_context)
        
        messages = []
        celery_agents_dispatched = []  # Track agents using Celery

        async def _send_system_message(text: str) -> None:
            if not text:
                return
            message_record = database_client.insert_chat_message(
                thread_id=thread_id,
                role="assistant",
                content=text,
                author="System",
                payload={"system": True},
                organization_id=organization.get("id") if organization else None,
                user_id=user_context.user_id,
            )

            if message_record:
                system_message = {
                    "id": str(message_record.get("id")),
                    "role": "assistant",
                    "content": text,
                    "author": message_record.get("author") or "System",
                    "created_at": message_record.get("created_at"),
                    "attachments": [],
                }
                if hasattr(system_message["created_at"], "isoformat"):
                    system_message["created_at"] = system_message["created_at"].isoformat()
                await notify_new_message(user_context.user_id, thread_id, system_message)
                messages.append(system_message)

        async def _cancel_active_job(agent_key: str) -> bool:
            job_record = database_client.get_active_agent_job_for_thread(
                auth_user_id=user_id,
                thread_id=thread_id,
                agent_key=agent_key,
            )

            if not job_record:
                return False

            job_id = job_record.get("id")
            metadata = dict(job_record.get("metadata") or {})
            metadata["cancel_requested_at"] = datetime.now(timezone.utc).isoformat()
            metadata["cancel_requested_by"] = user_id

            database_client.update_agent_job(
                job_id,
                status="cancelled",
                finished_at=datetime.now(timezone.utc),
                progress=1.0,
                metadata=metadata,
            )

            try:
                celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")
                logger.info("Cancelled job %s for agent '%s'", job_id, agent_key)
            except Exception as revoke_error:
                logger.warning(
                    "Failed to revoke job %s for agent '%s': %s",
                    job_id,
                    agent_key,
                    revoke_error,
                )

            try:
                await notify_chat_status(user_id, thread_id, "agent_cancelled", {"agent": agent_key})
                await notify_chat_status(user_id, thread_id, "agent_processing_completed")
            except Exception as ws_error:
                logger.warning(
                    "Failed to send cancellation status for agent '%s': %s",
                    agent_key,
                    ws_error,
                )

            await _send_system_message(f"Stopped {agent_key.title()} as requested.")
            return True
        
        # Get thread to check if it's a group chat
        thread = database_client.get_chat_thread(thread_id)
        thread_kind = thread.get("kind", "agent") if thread else "agent"

        cancel_requested, cancel_target = _parse_cancel_command(message_text)
        if cancel_requested:
            matched_any = False
            candidate_agents = agent_keys
            if cancel_target:
                target_normalised = cancel_target.casefold()
                candidate_agents = [
                    key
                    for key in agent_keys
                    if target_normalised in {key.casefold(), key.replace("-", " ").casefold()}
                ]

            if thread_kind == "group" and len(agent_keys) > 1 and not cancel_target:
                await _send_system_message("Specify which agent to stop (e.g., 'stop adam').")
                return {"messages": messages, "thread_id": thread_id, "cancelled": False}

            for candidate in candidate_agents:
                cancelled = await _cancel_active_job(candidate)
                matched_any = matched_any or cancelled

            if matched_any:
                return {"messages": messages, "thread_id": thread_id, "cancelled": True}

            # No active jobs matched; inform the user to avoid silent failures
            await notify_chat_status(user_id, thread_id, "agent_processing_completed")
            await _send_system_message("No active jobs to cancel right now.")
            return {"messages": messages, "thread_id": thread_id, "cancelled": False}
        
        # Process agent(s) assigned to this thread
        for agent_key in agent_keys:
            if not user_context.is_agent_enabled(agent_key):
                logger.info("Agent '%s' is disabled for user %s", agent_key, user_id)
                continue
                
            try:
                logger.info(
                    "Processing agent '%s' for message in thread %s", agent_key, thread_id
                )
                
                # Send typing indicator for this specific agent
                try:
                    await notify_chat_status(user_id, thread_id, "agent_typing", {"agent": agent_key})
                    logger.debug("Sent typing indicator for agent '%s'", agent_key)
                except Exception as ws_error:
                    logger.warning(
                        "Failed to send typing indicator for agent '%s': %s",
                        agent_key,
                        ws_error,
                    )
                
                # API chat should ALWAYS use Celery workers - skip direct chat interface
                logger.debug(
                    "API mode: Skipping direct chat interface for '%s'; dispatching via Celery",
                    agent_key,
                )

                job_metadata = {
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "source": "chat_api",
                    "agent_key": agent_key,
                    "author": author_label,
                }
                if normalised_attachments:
                    job_metadata["attachments"] = normalised_attachments
                payload = {
                    "cli_args": {
                        "message": message_text,
                        "author": author_label,
                    },
                    "env_overrides": {},
                    "extra_args": [],
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "metadata": job_metadata,
                }
                if normalised_attachments:
                    payload["cli_args"]["attachments"] = normalised_attachments

                job_record = database_client.create_agent_job(
                    user_context.user_id,
                    agent_key,
                    payload=payload,
                    metadata=job_metadata,
                )

                job_id = (job_record or {}).get("id") or str(uuid.uuid4())

                if job_record:
                    updated_metadata = dict(job_record.get("metadata") or {})
                    updated_metadata.setdefault("thread_id", thread_id)
                    updated_metadata.setdefault("agent_key", agent_key)
                    updated_metadata.setdefault("session_id", session_id)
                    updated_metadata["job_id"] = job_id
                    database_client.update_agent_job(job_id, metadata=updated_metadata)
                    payload["metadata"] = updated_metadata
                else:
                    payload["metadata"] = {**job_metadata, "job_id": job_id}

                run_agent_job.delay(
                    job_id=job_id,
                    auth_user_id=user_id,
                    agent_key=agent_key,
                    payload=payload,
                )

                logger.info("Dispatched agent '%s' with job_id=%s", agent_key, job_id)
                
                # Track that this agent is using Celery
                celery_agents_dispatched.append(agent_key)
                
                # For now, we'll let the agent task handle the response
                # The agent will store its response in the database and send WebSocket notifications
                
            except Exception as agent_error:
                logger.exception("Error processing agent '%s': %s", agent_key, agent_error)
                
                # Clear typing indicator on agent error
                try:
                    await notify_chat_status(user_id, thread_id, "agent_error", {"agent": agent_key, "error": str(agent_error)})
                    logger.debug("Cleared typing indicator for failed agent '%s'", agent_key)
                except Exception as ws_error:
                    logger.warning(
                        "Failed to clear typing indicator for agent '%s': %s",
                        agent_key,
                        ws_error,
                    )
                
                continue
        
        # Notify that agent processing is completed (removes global typing indicators)
        # Only send this if no Celery agents were dispatched (they handle their own completion)
        if not celery_agents_dispatched:
            try:
                await notify_chat_status(user_id, thread_id, "agent_processing_completed")
                logger.debug(
                    "Sent agent_processing_completed notification for thread %s", thread_id
                )
            except Exception as ws_error:
                logger.warning(
                    "Failed to send agent_processing_completed notification for thread %s: %s",
                    thread_id,
                    ws_error,
                )
        else:
            logger.debug(
                "Skipping agent_processing_completed; Celery agents %s will handle completion",
                celery_agents_dispatched,
            )
        
        return {"messages": messages, "thread_id": thread_id, "celery_agents_dispatched": celery_agents_dispatched}
        
    except Exception as exc:
        logger.exception("Error in process_agents_for_message: %s", exc)
        
        # Clear any typing indicators on global error
