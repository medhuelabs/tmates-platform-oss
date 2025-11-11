"""Dana API adapter delegating to the shared SDK helper."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.auth import UserContext
from app.agents.dana.brain import run_prompt
from app.sdk.agents.tmates_agents_sdk import run_agent_api_request


def process_api_request(request: Dict[str, Any], user_context: Optional[UserContext] = None) -> Dict[str, Any]:
    return run_agent_api_request(
        agent_key="dana",
        author_name="Dana",
        request=request,
        user_context=user_context,
        run_prompt=run_prompt,
    )