"""Leo API adapter built on top of the shared TmatesAgentsSDK helper."""

from typing import Any, Dict, Optional

from app.auth import UserContext
from app.agents.leo.brain import run_prompt
from app.sdk.agents.tmates_agents_sdk import run_agent_api_request


def _build_leo_context(request: Dict[str, Any], user_id: str, session_id: str) -> Dict[str, Any]:
    metadata = request.get("metadata") or {}
    return {
        "thread_id": request.get("thread_id"),
        "user_id": user_id,
        "job_id": metadata.get("job_id"),
        "agent_key": "leo",
        "metadata": metadata,
        "author": request.get("author", "User"),
    }


def process_api_request(request: Dict[str, Any], user_context: Optional[UserContext] = None) -> Dict[str, Any]:
    return run_agent_api_request(
        agent_key="leo",
        author_name="Leo",
        request=request,
        user_context=user_context,
        run_prompt=run_prompt,
        include_generated_attachments=True,
        context_builder=_build_leo_context,
    )
