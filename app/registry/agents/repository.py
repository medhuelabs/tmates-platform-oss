"""Repositories that expose domain models from code-based sources."""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Dict, Iterable, Optional

from ...config import INSTALLED_AGENTS

from .models import AgentDefinition


class AgentRepository:
    """Load agent definitions from the codebase."""

    def __init__(self, agents_dir: Optional[Path] = None):
        if agents_dir is None:
            agents_dir = Path(__file__).resolve().parents[2] / "agents"
        self._agents_dir = Path(agents_dir)

    def _manifest_path(self, key: str) -> Path:
        return self._agents_dir / key / "manifest.yaml"

    def _load_manifest(self, key: str) -> Optional[dict]:
        manifest_path = self._manifest_path(key)
        if not manifest_path.exists():
            return None
        with manifest_path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def _load_docs(self, key: str) -> Optional[str]:
        try:
            module = __import__(f"app.agents.{key}.config", fromlist=["DOCS", "ICON"])
        except ModuleNotFoundError:
            return None
        return getattr(module, "DOCS", None)

    def _load_icon(self, key: str) -> Optional[str]:
        try:
            module = __import__(f"app.agents.{key}.config", fromlist=["ICON"])
        except ModuleNotFoundError:
            return None
        return getattr(module, "ICON", None)

    def _build_definition(self, key: str) -> Optional[AgentDefinition]:
        manifest = self._load_manifest(key)
        if not isinstance(manifest, dict):
            return None
        path = self._agents_dir / key
        docs = self._load_docs(key)
        icon = self._load_icon(key)
        return AgentDefinition.from_manifest(key, path, manifest, docs=docs, icon=icon)

    def keys(self) -> Iterable[str]:
        seen: set[str] = set()
        for key in INSTALLED_AGENTS:
            cleaned = (key or "").strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                yield cleaned

        try:
            directory_entries = sorted(p.name for p in self._agents_dir.iterdir() if p.is_dir())
        except FileNotFoundError:
            directory_entries = []

        for key in directory_entries:
            cleaned = (key or "").strip()
            if not cleaned or cleaned in seen or cleaned.startswith("_"):
                continue
            seen.add(cleaned)
            yield cleaned

    def all(self) -> Dict[str, AgentDefinition]:
        definitions: Dict[str, AgentDefinition] = {}
        for key in self.keys():
            definition = self._build_definition(key)
            if definition:
                definitions[key] = definition
        return definitions

    def get(self, key: str) -> Optional[AgentDefinition]:
        cleaned = (key or "").strip()
        if not cleaned:
            return None
        return self._build_definition(cleaned)
