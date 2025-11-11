import json
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_current_user_id
from app.db.client import get_database_client
from app.registry.agents.store import AgentStore
from app.config import CONFIG
from app.billing import BillingManager

router = APIRouter()
_agent_store = AgentStore()

TEAM_CHAT_TITLE = "Team Chat"
TEAM_CHAT_SLUG = "group:all"
TEAM_CHAT_KIND = "group"


def _normalize_agent_keys(raw_value) -> List[str]:
    if isinstance(raw_value, list):
        return [str(entry) for entry in raw_value if entry]
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return [str(entry) for entry in parsed if entry]
        except json.JSONDecodeError:
            return []
    return []


def _ensure_team_chat_thread(db, user_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
    """Fetch or create the team chat thread for this user."""

    try:
        threads = db.list_chat_threads(
            user_id,
            organization_id=organization_id,
            limit=200,
        )
    except Exception as exc:
        print(f"Failed to list threads when ensuring team chat: {exc}")
        threads = []

    for thread in threads or []:
        metadata = thread.get("metadata") or {}
        slug = metadata.get("slug")
        title = (thread.get("title") or "").strip().lower()
        if slug == TEAM_CHAT_SLUG or title == TEAM_CHAT_TITLE.lower():
            return thread

    try:
        thread = db.create_chat_thread(
            auth_user_id=user_id,
            organization_id=organization_id,
            title=TEAM_CHAT_TITLE,
            kind=TEAM_CHAT_KIND,
            agent_keys=[],
            metadata={
                "slug": TEAM_CHAT_SLUG,
                "created_via": "team_chat_auto",
                "agent_keys": [],
            },
        )
        if thread:
            print(f"Created team chat thread {thread.get('id')} for user {user_id}")
        return thread
    except Exception as exc:
        print(f"Failed to create team chat thread: {exc}")
        return None


def _sync_team_chat_agents(
    db,
    thread: Dict[str, Any],
    desired_agent_keys: List[str],
) -> bool:
    """Ensure the team chat thread tracks the provided agent keys."""

    sorted_keys = sorted({key for key in desired_agent_keys if key})

    current_keys = _normalize_agent_keys(thread.get("agent_keys"))
    metadata = thread.get("metadata") or {}
    metadata_keys = _normalize_agent_keys(metadata.get("agent_keys"))

    if sorted(current_keys) == sorted_keys and sorted(metadata_keys) == sorted_keys:
        return False

    metadata = dict(metadata)
    metadata["agent_keys"] = sorted_keys
    metadata.setdefault("slug", TEAM_CHAT_SLUG)

    updates = {
        "agent_keys": sorted_keys,
        "metadata": metadata,
    }

    try:
        updated = db.update_chat_thread(thread.get("id"), updates)
        if updated:
            thread.update(updated)
        else:
            thread.update(updates)
        return True
    except Exception as exc:
        print(f"Failed to sync team chat agents: {exc}")
        return False


def _post_team_chat_event(
    db,
    *,
    thread_id: str,
    organization_id: str,
    user_id: str,
    agent_key: str,
    agent_name: str,
    event: str,
    message: str,
) -> None:
    """Insert a system message describing a membership change."""

    payload = {
        "event": event,
        "agent_key": agent_key,
        "agent_name": agent_name,
    }

    try:
        db.insert_chat_message(
            thread_id=thread_id,
            role="system",
            content=message,
            author=None,
            payload=payload,
            organization_id=organization_id,
            user_id=user_id,
        )
    except Exception as exc:
        print(f"Failed to post team chat event message: {exc}")

    try:
        db.touch_chat_thread(thread_id)
    except Exception as exc:
        print(f"Failed to touch team chat thread {thread_id}: {exc}")


@router.get("/agents/store", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
def get_agent_store(user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    """Get all available agents and user's organization agents (like an app store)."""
    
    try:
        # Get database client
        db = get_database_client()
        if not db:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database not available"
            )
        
        # Get user's organization
        org = db.get_user_organization(user_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User organization not found"
            )
        
        # Get organization agents (which agents are "hired")
        org_agents = db.get_organization_agents(org['id'])
        org_agent_keys = {agent["key"] for agent in org_agents}
        
        available_agents: List[Dict[str, Any]] = []
        source = "filesystem"

        if getattr(CONFIG, "agent_catalog_enabled", False):
            try:
                catalog_entries = db.list_agent_catalog_agents(
                    environment="current",  # This parameter is no longer used
                    organization_id=org['id'],
                )
            except AttributeError:
                catalog_entries = []

            if catalog_entries:
                source = "catalog"
                for entry in catalog_entries:
                    key = entry.get("key")
                    available_agents.append({
                        "key": key,
                        "name": entry.get("name") or key,
                        "description": entry.get("description") or "",
                        "icon": entry.get("icon") or "🤖",
                        "category": entry.get("category"),
                        "version": entry.get("version"),
                        "status": entry.get("status"),
                        "hired": key in org_agent_keys,
                        "metadata": {
                            "audience": entry.get("audience"),
                            "manifest": entry.get("manifest"),
                            "published_at": entry.get("published_at"),
                            "bundle_url": entry.get("bundle_url"),
                            "bundle_checksum": entry.get("bundle_checksum"),
                            "bundle_signature": entry.get("bundle_signature"),
                            "signature_algorithm": entry.get("signature_algorithm"),
                        },
                    })

        if not available_agents:
            try:
                for agent in _agent_store.get_available_agents() or []:
                    available_agents.append({
                        "key": agent.key,
                        "name": agent.name,
                        "description": agent.description,
                        "icon": agent.icon or "🤖",
                        "hired": agent.key in org_agent_keys,
                    })
            except Exception as e:
                print(f"Error loading agents from store: {e}")
                return {
                    "available_agents": [],
                    "hired_count": len(org_agent_keys),
                    "available_count": 0,
                    "organization": org['name'],
                    "source": source,
                }

        return {
            "available_agents": available_agents,
            "hired_count": len(org_agent_keys),
            "available_count": len(available_agents),
            "organization": org['name'],
            "source": source,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Agent store error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load agent store"
        )


@router.post("/agents/manage", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
def manage_organization_agent(
    request: Dict[str, str],
    user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    """Add or remove an agent for the current user's organization."""
    
    try:
        # Get database client
        db = get_database_client()
        if not db:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database not available"
            )
        
        # Validate request
        agent_key = request.get("agent_key", "").strip()
        action = request.get("action", "").strip()
        
        if not agent_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="agent_key is required"
            )
        
        if action not in ["add", "remove"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="action must be 'add' or 'remove'"
            )
        
        # Get user's organization
        org = db.get_user_organization(user_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User organization not found"
            )
        
        try:
            org_agents_existing = db.get_organization_agents(org['id']) or []
        except Exception as agent_list_exc:
            print(f"Failed to load organization agents for {org['id']}: {agent_list_exc}")
            org_agents_existing = []

        # Verify agent exists either in the store (current definition) or organization records
        agent = _agent_store.get_agent(agent_key)
        org_agent_record: Optional[Dict[str, Any]] = None
        if not agent:
            try:
                org_agent_record = db.get_agent_by_key(org['id'], agent_key)
            except Exception as lookup_exc:
                print(f"Failed to lookup agent {agent_key} in organization {org['id']}: {lookup_exc}")

        if action == "add" and not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent '{agent_key}' not found"
            )

        if action == "remove" and not agent and not org_agent_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent '{agent_key}' not found"
            )
        
        if action == "add":
            billing_manager = BillingManager(db)
            plan_context = None
            if billing_manager.enabled:
                try:
                    plan_context = billing_manager.get_plan_context(
                        org['id'],
                        active_agents=len(org_agents_existing),
                    )
                except Exception as plan_exc:
                    print(f"Failed to resolve billing plan for org {org['id']}: {plan_exc}")
                if plan_context is not None:
                    limit_error = billing_manager.agent_limit_error(
                        plan_context,
                        active_agents=len(org_agents_existing) + 1,
                    )
                    if limit_error:
                        raise HTTPException(
                            status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail=limit_error,
                        )

            # Add agent to organization
            agent_data = {
                'name': agent.name,
                'description': agent.description,
                'agent_type': 'assistant',
                'config': {}
            }
            success = db.add_agent_to_organization(org['id'], agent_key, agent_data, user_id)
            if success:
                existing_thread = None
                try:
                    potential_threads = db.list_chat_threads(
                        user_id,
                        organization_id=org['id'],
                        limit=200,
                    )
                except Exception as list_error:
                    potential_threads = []
                    print(f"Failed to list chat threads for agent {agent_key}: {list_error}")

                for thread in potential_threads or []:
                    thread_agent_keys = _normalize_agent_keys(thread.get("agent_keys"))
                    if len(thread_agent_keys) == 1 and thread_agent_keys[0] == agent_key:
                        if existing_thread is None:
                            existing_thread = thread

                if existing_thread:
                    metadata = existing_thread.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata.update({
                        'agent_name': agent.name,
                        'agent_description': agent.description,
                        'agent_key': agent_key,
                    })
                    metadata.setdefault('created_via', 'agent_addition')

                    kind = existing_thread.get("kind") or "agent"
                    if kind == "agent_chat":
                        kind = "agent"

                    updates = {
                        "title": agent.name,
                        "metadata": metadata,
                        "agent_keys": [agent_key],
                        "kind": kind,
                    }

                    try:
                        updated_thread = db.update_chat_thread(existing_thread.get("id"), updates)
                        if updated_thread:
                            existing_thread = updated_thread
                        print(f"Reused chat thread {existing_thread.get('id')} for agent {agent_key}")
                    except Exception as update_error:
                        print(f"Failed to update chat thread {existing_thread.get('id')} for agent {agent_key}: {update_error}")

                    try:
                        db.touch_chat_thread(existing_thread.get("id"))
                    except Exception as touch_error:
                        print(f"Failed to touch chat thread {existing_thread.get('id')} for agent {agent_key}: {touch_error}")
                else:
                    try:
                        thread = db.create_chat_thread(
                            auth_user_id=user_id,
                            organization_id=org['id'],
                            title=agent.name,
                            kind="agent",
                            agent_keys=[agent_key],
                            metadata={
                                'agent_name': agent.name,
                                'agent_description': agent.description,
                                'agent_key': agent_key,
                                'created_via': 'agent_addition'
                            }
                        )
                        if thread:
                            existing_thread = thread
                            print(f"Created chat thread {thread.get('id')} for agent {agent_key}")
                        else:
                            print(f"Failed to create chat thread for agent {agent_key}: No thread returned")
                    except Exception as create_error:
                        print(f"Failed to create chat thread for agent {agent_key}: {create_error}")
                        # Don't fail the entire operation if chat thread creation fails
                
                try:
                    team_thread = _ensure_team_chat_thread(db, user_id, org['id'])
                    if team_thread:
                        previous_keys = set(_normalize_agent_keys(team_thread.get("agent_keys")))
                        previous_keys.update(
                            _normalize_agent_keys((team_thread.get("metadata") or {}).get("agent_keys"))
                        )
                        org_agents_current = db.get_organization_agents(org['id']) or []
                        desired_keys = [entry.get("key") for entry in org_agents_current if entry.get("key")]
                        if agent_key not in desired_keys:
                            desired_keys.append(agent_key)
                        changed = _sync_team_chat_agents(db, team_thread, desired_keys)
                        if agent_key not in previous_keys and agent_key in desired_keys and changed:
                            _post_team_chat_event(
                                db,
                                thread_id=team_thread.get("id"),
                                organization_id=org['id'],
                                user_id=user_id,
                                agent_key=agent_key,
                                agent_name=agent.name,
                                event="agent_join",
                                message=f"{agent.name} joined the chat.",
                            )
                except Exception as team_exc:
                    print(f"Failed to update team chat after adding {agent_key}: {team_exc}")

                return {
                    "success": True,
                    "action": "added",
                    "agent_key": agent_key,
                    "message": f"Agent '{agent.name}' has been added to your organization"
                }
            else:
                return {
                    "success": False,
                    "action": "add_failed",
                    "agent_key": agent_key,
                    "message": f"Agent '{agent.name}' is already in your organization"
                }
        
        else:  # action == "remove"
            # Remove agent from organization
            agent_name = (
                agent.name
                if agent is not None
                else (org_agent_record.get("name") if org_agent_record else agent_key)
            )
            success = db.remove_agent_from_organization(org['id'], agent_key)
            if success:
                try:
                    team_thread = _ensure_team_chat_thread(db, user_id, org['id'])
                    if team_thread:
                        previous_keys = set(_normalize_agent_keys(team_thread.get("agent_keys")))
                        previous_keys.update(
                            _normalize_agent_keys((team_thread.get("metadata") or {}).get("agent_keys"))
                        )
                        org_agents_current = db.get_organization_agents(org['id']) or []
                        desired_keys = [entry.get("key") for entry in org_agents_current if entry.get("key")]
                        if agent_key in desired_keys:
                            desired_keys = [key for key in desired_keys if key != agent_key]
                        changed = _sync_team_chat_agents(db, team_thread, desired_keys)
                        if agent_key in previous_keys and agent_key not in desired_keys and changed:
                            _post_team_chat_event(
                                db,
                                thread_id=team_thread.get("id"),
                                organization_id=org['id'],
                                user_id=user_id,
                                agent_key=agent_key,
                                agent_name=agent_name,
                                event="agent_leave",
                                message=f"{agent_name} left the chat.",
                            )
                except Exception as team_exc:
                    print(f"Failed to update team chat after removing {agent_key}: {team_exc}")
                return {
                    "success": True,
                    "action": "removed",
                    "agent_key": agent_key,
                    "message": f"Agent '{agent_name}' has been removed from your organization"
                }
            else:
                return {
                    "success": False,
                    "action": "remove_failed",
                    "agent_key": agent_key,
                    "message": f"Agent '{agent_name}' was not in your organization"
                }
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Agent management error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to manage organization agent"
        )
