"""Adam API adapter built on the shared TmatesAgentsSDK helper."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.auth import UserContext
from app.agents.adam.brain import run_prompt
from app.sdk.agents.tmates_agents_sdk import run_agent_api_request


def process_api_request(request: Dict[str, Any], user_context: Optional[UserContext] = None) -> Dict[str, Any]:
    return run_agent_api_request(
        agent_key="adam",
        author_name="Adam",
        request=request,
        user_context=user_context,
        run_prompt=run_prompt,
        vision_enabled=True,
    )
