"""Agent definition model for codebase discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


@dataclass(slots=True)
class AgentDefinition:
    """Represents an agent package defined in the codebase."""

    key: str
    name: str
    description: str
    path: Path
    manifest: Dict[str, Any] = field(default_factory=dict)
    docs: Optional[str] = None
    icon: Optional[str] = None
    required_env: Iterable[Dict[str, Any]] = field(default_factory=list)
    optional_env: Iterable[Dict[str, Any]] = field(default_factory=list)
    playbook_required: Iterable[Dict[str, Any]] = field(default_factory=list)
    playbook_optional: Iterable[Dict[str, Any]] = field(default_factory=list)
    tools: Iterable[Dict[str, Any]] = field(default_factory=list)
    tasks: Iterable[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_manifest(cls, key: str, path: Path, manifest: Dict[str, Any], *, docs: Optional[str] = None, icon: Optional[str] = None) -> "AgentDefinition":
        name = manifest.get("name") or key
        description = manifest.get("description") or ""
        env_block = manifest.get("env") or {}
        playbook_block = manifest.get("playbook") or {}
        tools_block = manifest.get("tools") or []
        tasks_block = manifest.get("tasks") or []

        return cls(
            key=key,
            name=name,
            description=description,
            path=path,
            manifest=manifest,
            docs=docs or manifest.get("docs"),
            icon=icon or manifest.get("icon"),
            required_env=env_block.get("required", []),
            optional_env=env_block.get("optional", []),
            playbook_required=playbook_block.get("required_params", []),
            playbook_optional=playbook_block.get("optional_params", []),
            tools=tools_block,
            tasks=tasks_block,
        )
