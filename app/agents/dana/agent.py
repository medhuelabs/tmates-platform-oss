"""Agent entry-point for Dana."""

from __future__ import annotations

from typing import Any, Dict

from app.registry.agents.base import AgentBase


class DanaAgent(AgentBase):
    """Gmail-savvy teammate leveraging the shared OpenAI runner."""

    key = "dana"

    def run_api(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Service API call coming from the mobile/web clients."""

        from .interface.api import process_api_request

        return process_api_request(request, user_context=self.user_context)


AGENT_CLASS = DanaAgent
