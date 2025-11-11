"""Nolan API adapter leveraging the shared TmatesAgentsSDK helper."""

import logging
from typing import Any, Dict, Optional

from app.auth import UserContext
from app.agents.nolan.brain import run_prompt
from app.sdk.agents.tmates_agents_sdk import run_agent_api_request


logger = logging.getLogger(__name__)


def _build_nolan_context(request: Dict[str, Any], user_id: str, session_id: str) -> Dict[str, Any]:
    metadata = request.get("metadata") or {}
    attachments = request.get("attachments") or metadata.get("attachments") or []
    if attachments and "attachments" not in metadata:
        metadata = dict(metadata)
        metadata["attachments"] = attachments

    try:
        attachment_names = [
            str(item.get("name") or item.get("filename") or "")
            for item in attachments
            if isinstance(item, dict)
        ]
        logger.debug(
            "Nolan API request metadata",
            extra={
                "thread_id": request.get("thread_id"),
                "job_id": metadata.get("job_id"),
                "attachments_count": len(attachments),
                "attachment_names": attachment_names,
            },
        )
    except Exception:
        logger.debug("Unable to summarize attachments for logging", exc_info=True)

    return {
        "thread_id": request.get("thread_id"),
        "user_id": user_id,
        "job_id": metadata.get("job_id"),
        "agent_key": "nolan",
        "metadata": metadata,
        "author": request.get("author", "User"),
        "attachments": attachments,
    }


def process_api_request(request: Dict[str, Any], user_context: Optional[UserContext] = None) -> Dict[str, Any]:
    return run_agent_api_request(
        agent_key="nolan",
        author_name="Nolan",
        request=request,
        user_context=user_context,
        run_prompt=run_prompt,
        include_generated_attachments=True,
        context_builder=_build_nolan_context,
    )
