"""Tests for agent store routes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from app.api.routes import agents as agents_routes


class StubAgent:
    def __init__(self, key: str, *, name: str = None, description: str = "", icon: str | None = None):
        self.key = key
        self.name = name or key.title()
        self.description = description
        self.icon = icon


class StubAgentStore:
    def __init__(self, agents: List[StubAgent]):
        self._agents = agents

    def get_available_agents(self):
        return list(self._agents)

    def get_agent(self, key: str):
        return next((agent for agent in self._agents if agent.key == key), None)


_DEFAULT_ORG = {"id": "org-1", "name": "Acme"}


class StubDB:
    def __init__(self, *, org: object = ..., org_agents: List[Dict[str, str]] | None = None):
        self._org = _DEFAULT_ORG if org is ... else org
        self._org_agents = org_agents or [{"key": "adam"}]
        self._catalog_entries: List[Dict[str, Any]] = []

    def set_catalog(self, entries: List[Dict[str, Any]]):
        self._catalog_entries = entries

    def get_user_organization(self, user_id: str):
        return self._org

    def get_organization_agents(self, org_id: str):
        if not self._org:
            return []
        assert org_id == self._org["id"]
        return self._org_agents

    def list_agent_catalog_agents(self, **kwargs):
        return list(self._catalog_entries)


def _patch_basics(monkeypatch: pytest.MonkeyPatch, db: StubDB, agents: List[StubAgent], *, catalog_enabled: bool = False):
    monkeypatch.setattr(agents_routes, "get_database_client", lambda: db)
    monkeypatch.setattr(agents_routes, "_agent_store", StubAgentStore(agents))
    monkeypatch.setattr(agents_routes, "CONFIG", SimpleNamespace(agent_catalog_enabled=catalog_enabled))


def test_get_agent_store_returns_filesystem_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    db = StubDB()
    agent = StubAgent("adam", name="Adam", description="Helper")
    _patch_basics(monkeypatch, db, [agent])

    result = agents_routes.get_agent_store(user_id="user-1")

    assert result["source"] == "filesystem"
    assert result["available_count"] == 1
    assert result["available_agents"][0]["key"] == "adam"
    assert result["available_agents"][0]["hired"] is True


def test_get_agent_store_uses_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    db = StubDB()
    db.set_catalog(
        [
            {
                "key": "dana",
                "name": "Dana",
                "description": "Analyst",
                "icon": "🧠",
                "category": "ops",
                "version": "1.0.0",
                "status": "published",
                "audience": "beta",
            }
        ]
    )
    _patch_basics(monkeypatch, db, [], catalog_enabled=True)

    result = agents_routes.get_agent_store(user_id="user-1")

    assert result["source"] == "catalog"
    assert result["available_agents"][0]["key"] == "dana"
    assert result["available_agents"][0]["hired"] is False
    assert result["available_agents"][0]["metadata"]["audience"] == "beta"


def test_get_agent_store_missing_org(monkeypatch: pytest.MonkeyPatch) -> None:
    db = StubDB(org=None)
    monkeypatch.setattr(agents_routes, "get_database_client", lambda: db)

    with pytest.raises(HTTPException) as exc:
        agents_routes.get_agent_store(user_id="user-1")

    assert exc.value.status_code == 400
