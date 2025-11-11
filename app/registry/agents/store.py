"""Agent Store - Dynamic agent discovery and hiring system."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .models import AgentDefinition
from .repository import AgentRepository


class AgentStore:
    """Manages agent discovery for the platform."""

    def __init__(self, agents_dir: str | Path | None = None) -> None:
        if agents_dir is None:
            agents_dir = Path(__file__).resolve().parents[2] / "agents"
        self._agents_dir = Path(agents_dir)
        self._repository = AgentRepository(self._agents_dir)

    def discover_agents(self) -> Dict[str, AgentDefinition]:
        """Return a mapping of agent key to definition."""
        return self._repository.all()

    def get_agent(self, agent_key: str) -> Optional[AgentDefinition]:
        """Return a single agent definition if it exists."""
        cleaned = (agent_key or "").strip()
        if not cleaned:
            return None
        return self._repository.get(cleaned)

    def get_available_agents(self) -> List[AgentDefinition]:
        """Return all discoverable agent definitions."""
        return list(self.discover_agents().values())

    def is_agent_available(self, agent_key: str) -> bool:
        """Check if an agent exists and is discoverable."""
        return self.get_agent(agent_key) is not None
