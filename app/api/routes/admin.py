"""
API endpoint for cleaning up removed agents.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any

from app.api.dependencies import get_current_user_id
from app.db.client import get_database_client
from app.registry.agents.store import AgentStore

router = APIRouter()


@router.post("/admin/cleanup-agents", status_code=status.HTTP_200_OK)
def cleanup_removed_agents(
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Clean up database references to agents that no longer exist.
    
    This endpoint marks organization agents as inactive for any agents that are
    no longer available in the agent store.
    
    Requires authentication.
    """
    
    try:
        # Get available agents from the store
        agent_store = AgentStore()
        available_agents = agent_store.discover_agents()
        available_keys = set(available_agents.keys())
        
        # Get database client
        db = get_database_client()
        if not db:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not connect to database"
            )
        
        removed_count = 0

        if not hasattr(db, "client") or not hasattr(db.client, "table"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Supabase client not configured"
            )

        result = db.client.table("agents").select("*").execute()
        org_agents = result.data or []

        for record in org_agents:
            agent_key = record.get("key")
            org_id = record.get("organization_id")
            if not agent_key or not org_id:
                continue
            if agent_key in available_keys:
                continue
            if record.get("is_active", True) and db.remove_agent_from_organization(org_id, agent_key):
                removed_count += 1
        
        return {
            "success": True,
            "message": f"Successfully cleaned up {removed_count} agent references",
            "available_agents": sorted(available_keys),
            "removed_count": removed_count
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during cleanup: {str(e)}"
        )
