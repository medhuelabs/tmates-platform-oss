"""
Dynamic Agents API - Provides agent metadata dynamically

This endpoint replaces hard-coded agent definitions in mobile app.
"""

from typing import Dict, List, Any
from fastapi import APIRouter, Depends
from app.api.dependencies import get_current_user_id, get_database_with_user
from app.core.dynamic_agent_service import dynamic_agent_service
from app.core.agent_runner import resolve_user_context

router = APIRouter()


@router.get("/agents/metadata")
async def get_agents_metadata(
    database_user = Depends(get_database_with_user)
) -> Dict[str, Any]:
    """
    Get complete agents metadata with two-layer architecture information:
    - platform_agents: All agents available on this backend instance
    - user_enabled_agents: Agents this user has purchased/enabled
    - agents_metadata: Detailed metadata for each agent with access status
    """
    
    user_id, database = database_user
    user_context, _, _ = resolve_user_context(user_id)
    
    # Get both layers
    platform_agents = dynamic_agent_service.get_all_available_agent_keys()
    user_enabled_agents = dynamic_agent_service.get_enabled_agents_for_user(user_context)
    
    # Get metadata with access status for each agent
    agents_metadata = dynamic_agent_service.get_all_agents_metadata(user_context)
    
    return {
        "platform_agents": platform_agents,
        "user_enabled_agents": user_enabled_agents,
        "agents_metadata": agents_metadata,
        "platform_total": len(platform_agents),
        "user_enabled_count": len(user_enabled_agents),
        "access_summary": {
            "can_use": [key for key, meta in agents_metadata.items() if meta.get('can_use', False)],
            "not_enabled": [key for key, meta in agents_metadata.items() if meta.get('status') == 'not_enabled'],
            "not_installed": [key for key, meta in agents_metadata.items() if meta.get('status') == 'not_installed']
        }
    }


@router.get("/agents/{agent_key}/metadata")
async def get_agent_metadata(
    agent_key: str,
    database_user = Depends(get_database_with_user)
) -> Dict[str, Any]:
    """Get detailed metadata and access status for a specific agent."""
    
    user_id, database = database_user
    user_context, _, _ = resolve_user_context(user_id)
    
    if not dynamic_agent_service.is_agent_available_on_platform(agent_key):
        return {"error": f"Agent '{agent_key}' is not installed on this platform"}
    
    metadata = dynamic_agent_service.get_agent_metadata(agent_key)
    if not metadata:
        return {"error": f"Metadata not available for agent '{agent_key}'"}
    
    # Add comprehensive access status
    access_status = dynamic_agent_service.get_agent_access_status(agent_key, user_context)
    metadata.update(access_status)
    
    return metadata


@router.get("/agents/enabled")
async def get_enabled_agents(
    database_user = Depends(get_database_with_user)
) -> Dict[str, Any]:
    """Get only the agents this user can actually use (enabled AND available)."""
    
    user_id, database = database_user
    user_context, _, _ = resolve_user_context(user_id)
    
    user_enabled_agents = dynamic_agent_service.get_enabled_agents_for_user(user_context)
    
    # Only include agents that are both enabled for user AND available on platform
    usable_agents = [
        agent_key for agent_key in user_enabled_agents 
        if dynamic_agent_service.is_agent_available_on_platform(agent_key)
    ]
    
    usable_metadata = {}
    for agent_key in usable_agents:
        metadata = dynamic_agent_service.get_agent_metadata(agent_key)
        if metadata:
            access_status = dynamic_agent_service.get_agent_access_status(agent_key, user_context)
            metadata.update(access_status)
            usable_metadata[agent_key] = metadata
    
    return {
        "user_enabled_agents": user_enabled_agents,
        "usable_agents": usable_agents,
        "agents_metadata": usable_metadata,
        "user_enabled_count": len(user_enabled_agents),
        "usable_count": len(usable_agents),
        "unavailable_count": len(user_enabled_agents) - len(usable_agents)
    }


@router.get("/agents/platform")
async def get_platform_agents() -> Dict[str, Any]:
    """
    Get all agents available at the platform level (no user context needed).
    This shows what agents are installed on the backend.
    """
    
    platform_agents = dynamic_agent_service.get_all_available_agent_keys()
    
    platform_metadata = {}
    for agent_key in platform_agents:
        metadata = dynamic_agent_service.get_agent_metadata(agent_key)
        if metadata:
            platform_metadata[agent_key] = metadata
    
    return {
        "platform_agents": platform_agents,
        "agents_metadata": platform_metadata,
        "platform_total": len(platform_agents)
    }