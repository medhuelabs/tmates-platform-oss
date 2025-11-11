from typing import Any, Dict

from app.registry.agents.base import AgentBase


class AdamAgent(AgentBase):
    """Modern template agent exposing only the API entry point."""

    key = "adam"

    def run_api(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """API execution - structured request/response."""
        from .interface.api import process_api_request

        return process_api_request(request, user_context=self.user_context)


AGENT_CLASS = AdamAgent
