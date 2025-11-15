"""Agent Store - Dynamic agent discovery and hiring system."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from app.config import CONFIG
from app.db import get_database_client

from .models import AgentDefinition
from .repository import AgentRepository


class AgentStore:
    """Manages agent discovery for the platform."""

    def __init__(self, agents_dir: str | Path | None = None) -> None:
        if agents_dir is None:
            agents_dir = Path(__file__).resolve().parents[2] / "agents"
        self._agents_dir = Path(agents_dir)
        self._repository = AgentRepository(self._agents_dir)
        self._catalog_cache: Optional[Dict[str, AgentDefinition]] = None

    def _load_catalog_definitions(self) -> Dict[str, AgentDefinition]:
        if self._catalog_cache is not None:
            return self._catalog_cache

        if not getattr(CONFIG, "agent_catalog_enabled", False):
            self._catalog_cache = {}
            return self._catalog_cache

        db = get_database_client()
        if not db:
            self._catalog_cache = {}
            return self._catalog_cache

        environment = getattr(CONFIG, "agent_catalog_environment", "prod")
        try:
            entries = db.list_agent_catalog_agents(environment=environment)
        except Exception as exc:
            print(f"AgentStore: failed to load catalog agents: {exc}")
            self._catalog_cache = {}
            return self._catalog_cache

        catalog_definitions: Dict[str, AgentDefinition] = {}
        for entry in entries or []:
            key = (entry.get("key") or "").strip()
            if not key or key in catalog_definitions:
                continue

            manifest_snapshot = entry.get("manifest")
            manifest = manifest_snapshot if isinstance(manifest_snapshot, dict) else {}
            branding = manifest.get("branding") if isinstance(manifest.get("branding"), dict) else {}
            name = entry.get("name") or manifest.get("name") or key
            description = entry.get("description") or manifest.get("description") or f"{name} agent"
            fake_path = Path(f"/virtual/catalog/{key}")

            env_block = manifest.get("env") if isinstance(manifest.get("env"), dict) else {}
            playbook_block = manifest.get("playbook") if isinstance(manifest.get("playbook"), dict) else {}
            tools_block = manifest.get("tools") if isinstance(manifest.get("tools"), list) else []
            tasks_block = manifest.get("tasks") if isinstance(manifest.get("tasks"), list) else []

            definition = AgentDefinition(
                key=key,
                name=name,
                description=description,
                path=fake_path,
                manifest=manifest,
                docs=manifest.get("docs"),
                icon=branding.get("avatar_url") or entry.get("icon"),
                required_env=env_block.get("required", []),
                optional_env=env_block.get("optional", []),
                playbook_required=playbook_block.get("required_params", []),
                playbook_optional=playbook_block.get("optional_params", []),
                tools=tools_block,
                tasks=tasks_block,
            )
            catalog_definitions[key] = definition

        self._catalog_cache = catalog_definitions
        return catalog_definitions

    def discover_agents(self) -> Dict[str, AgentDefinition]:
        """Return a mapping of agent key to definition."""
        discovered = self._repository.all()
        catalog_definitions = self._load_catalog_definitions()
        for key, definition in catalog_definitions.items():
            discovered.setdefault(key, definition)
        return discovered

    def get_agent(self, agent_key: str) -> Optional[AgentDefinition]:
        """Return a single agent definition if it exists."""
        cleaned = (agent_key or "").strip()
        if not cleaned:
            return None
        definition = self._repository.get(cleaned)
        if definition:
            return definition
        catalog_definitions = self._load_catalog_definitions()
        return catalog_definitions.get(cleaned)

    def get_available_agents(self) -> List[AgentDefinition]:
        """Return all discoverable agent definitions."""
        return list(self.discover_agents().values())

    def is_agent_available(self, agent_key: str) -> bool:
        """Check if an agent exists and is discoverable."""
        return self.get_agent(agent_key) is not None
