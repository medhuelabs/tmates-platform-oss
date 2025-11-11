"""Teammate discovery endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_current_user_id, get_database_with_user
from app.api.schemas import Teammate
from app.core.agent_runner import resolve_user_context
from app.registry.agents.store import AgentStore

router = APIRouter()
_agent_store = AgentStore()


def _resolve_icon(agent_key: Optional[str]) -> Optional[str]:
    if not agent_key:
        return None
    definition = _agent_store.get_agent(agent_key)
    if definition and definition.icon:
        return definition.icon
    return "🤖"  # Generic fallback


def _resolve_manifest_branding(agent_key: Optional[str]) -> Optional[dict]:
    if not agent_key:
        return None
    definition = _agent_store.get_agent(agent_key)
    if definition and isinstance(definition.manifest, dict):
        branding = definition.manifest.get("branding")
        if isinstance(branding, dict):
            return branding
    return None


@router.get("/teammates", response_model=List[Teammate], status_code=status.HTTP_200_OK)
def list_teammates(
    context=Depends(get_database_with_user),
) -> List[Teammate]:
    """Return teammates (agents) available to the authenticated user."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        _, organization, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    agents = db.get_organization_agents(organization["id"])
    results: List[Teammate] = []
    for agent in agents or []:
        key = agent.get("key")
        if not key:
            continue
        metadata = {}
        branding = _resolve_manifest_branding(key)
        if branding:
            metadata["manifest"] = {"branding": branding}

        results.append(
            Teammate(
                key=key,
                name=agent.get("name") or key.title(),
                description=agent.get("description") or "",
                icon=_resolve_icon(key),
                detail_url=f"/agents/{key}",
                settings_url=f"/agents/{key}/settings",
                metadata=metadata or None,
            )
        )

    return results
