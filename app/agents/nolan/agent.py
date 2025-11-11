from typing import Any, Dict

from app.registry.agents.base import AgentBase


class NolanAgent(AgentBase):
    """Video generation agent powered by OpenAI Sora."""

    key = "nolan"

    def run_api(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """API execution - structured request/response."""
        from .interface.api import process_api_request

        return process_api_request(request, user_context=self.user_context)


AGENT_CLASS = NolanAgent
