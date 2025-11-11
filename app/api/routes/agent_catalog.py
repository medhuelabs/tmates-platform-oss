"""Agent catalog API endpoints for managed agent discovery."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_current_user_id
from app.config import CONFIG
from app.db.client import get_database_client

router = APIRouter()


def _serialize_catalog_entry(entry: Dict[str, Any], hired_keys: Optional[set[str]] = None) -> Dict[str, Any]:
    """Convert a catalog entry into API payload format."""

    key = entry.get("key")
    hired = key in hired_keys if hired_keys is not None else False
    payload: Dict[str, Any] = {
        "key": key,
        "name": entry.get("name") or key,
        "description": entry.get("description") or "",
        "icon": entry.get("icon"),
        "category": entry.get("category"),
        "version": entry.get("version"),
        "status": entry.get("status"),
        "hired": hired,
    }

    manifest = entry.get("manifest")
    audience = entry.get("audience")
    published_at = entry.get("published_at")
    bundle_url = entry.get("bundle_url")
    bundle_checksum = entry.get("bundle_checksum")
    bundle_signature = entry.get("bundle_signature")
    signature_algorithm = entry.get("signature_algorithm")

    meta: Dict[str, Any] = {}
    if manifest:
        meta["manifest"] = manifest
    if audience:
        meta["audience"] = audience
    if published_at:
        meta["published_at"] = published_at
    if bundle_url:
        meta["bundle_url"] = bundle_url
    if bundle_checksum:
        meta["bundle_checksum"] = bundle_checksum
    if bundle_signature:
        meta["bundle_signature"] = bundle_signature
    if signature_algorithm:
        meta["signature_algorithm"] = signature_algorithm

    if meta:
        payload["metadata"] = meta
    return payload


@router.get("/agents/catalog", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
def list_catalog_agents(
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """Return the catalog of agents visible to the current user."""

    if not getattr(CONFIG, "agent_catalog_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent catalog registry is disabled",
        )

    db = get_database_client()
    if not db:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database not available",
        )

    org = db.get_user_organization(user_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User organization not found",
        )

    # Environment is now determined by which database we're connected to
    entries = db.list_agent_catalog_agents(
        environment="current",  # This parameter is no longer used but kept for compatibility
        organization_id=org["id"],
    )

    hired_keys = {agent["key"] for agent in db.get_organization_agents(org["id"]) or []}
    serialized: List[Dict[str, Any]] = [
        _serialize_catalog_entry(entry, hired_keys=hired_keys) for entry in entries
    ]

    return {
        "environment": getattr(CONFIG, "environment", "production"),
        "agents": serialized,
        "organization": {
            "id": org["id"],
            "name": org.get("name"),
        },
    }
