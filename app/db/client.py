"""
Database client for multi-user automation system.
Handles user profiles, agent management, and run tracking.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from supabase import Client, create_client

from ..auth import UserContext
from ..config import CONFIG


def _dev_mode_enabled() -> bool:
    value = os.getenv("DEVELOPMENT_MODE", "").strip().lower()
    return value not in {"", "0", "false", "off", "none"}


class SupabaseDatabaseClient:
    """Database client for Supabase operations."""
    
    def __init__(self):
        self.supabase_url = os.getenv('SUPABASE_URL')
        
        # Prefer service role key when available to bypass RLS for server-side operations
        service_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        anon_key = os.getenv('SUPABASE_ANON_KEY')

        if service_key:
            self.supabase_key = service_key
            self.using_service_role = True
            if _dev_mode_enabled():
                print("DatabaseClient: Using service role key (development mode)")
        else:
            self.supabase_key = anon_key
            self.using_service_role = False

        if not self.supabase_url or not self.supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_ROLE_KEY) environment variables are required")

        if service_key and not _dev_mode_enabled():
            print("DatabaseClient: Using service role key for privileged access")

        self.client: Client = create_client(self.supabase_url, self.supabase_key)
    
    def setup_new_user(
        self,
        auth_user_id: str,
        email: str,
        organization_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Complete new user setup with organization, profile, and agents."""
        try:
            # Ensure users table record exists when running locally
            if _dev_mode_enabled():
                try:
                    self.client.table('users').insert({
                        'id': auth_user_id,
                        'email': email,
                    }).execute()
                except Exception as exc:
                    if "duplicate key" not in str(exc).lower():
                        print(f"Warning: Could not create users record: {exc}")

            profile_result = (
                self.client
                .table('user_profiles')
                .upsert({'auth_user_id': auth_user_id}, on_conflict='auth_user_id')
                .execute()
            )

            profile: Optional[Dict[str, Any]] = None
            if profile_result and profile_result.data:
                if isinstance(profile_result.data, list):
                    profile = profile_result.data[0] if profile_result.data else None
                elif isinstance(profile_result.data, dict):
                    profile = profile_result.data

            if not profile:
                profile = self.get_user_profile_by_auth_id(auth_user_id)

            if not profile:
                print("User profile upsert failed")
                return None

            existing_org = self.get_user_organization(auth_user_id)
            if existing_org:
                return {
                    'profile': profile,
                    'organization': existing_org,
                    'status': 'exists'
                }

            setup_result = self.client.rpc('setup_new_user', {
                'p_user_id': auth_user_id,
                'p_email': email,
                'p_display_name': organization_name,
            }).execute()

            organization: Optional[Dict[str, Any]] = None
            if setup_result and setup_result.data:
                organization = setup_result.data
                if isinstance(organization, list):
                    organization = organization[0] if organization else None

            if not organization:
                organization = self.get_user_organization(auth_user_id)

            if organization and organization.get("id"):
                try:
                    self.ensure_organization_subscription(organization["id"], "free")
                except Exception as subscription_exc:
                    print(f"Warning: failed to ensure default subscription for org {organization.get('id')}: {subscription_exc}")

            return {
                'profile': profile,
                'organization': organization,
                'status': 'success' if organization else 'pending'
            }

        except Exception as e:
            print(f"Error in setup_new_user: {e}")
            return None

    def create_user_profile(
        self,
        auth_user_id: str,
        email: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Create a new user profile record if one does not already exist."""
        try:
            # In development mode, ensure users table record exists
            if _dev_mode_enabled():
                try:
                    if email:
                        self.client.table('users').insert({
                            'id': auth_user_id,
                            'email': email
                        }).execute()
                except Exception as exc:
                    if "duplicate key" not in str(exc).lower():
                        print(f"Warning: Could not create users record: {exc}")

            result = self.client.table('user_profiles').insert({
                'auth_user_id': auth_user_id,
            }).execute()

            profile: Optional[Dict[str, Any]] = None
            if result.data:
                profile = result.data[0]
                print(f"User profile created successfully: {profile['id']}")
                self._initialize_user_agents(auth_user_id)
                return profile

            return self.get_user_profile_by_auth_id(auth_user_id)

        except Exception as exc:
            print(f"Error creating user profile: {exc}")
            return None

    def _initialize_user_agents(self, auth_user_id: str):
        """Legacy hook retained for compatibility; agents are organization-scoped now."""
        try:
            org = self.get_user_organization(auth_user_id)
            if not org:
                print(f"No organization found while initializing agents for user {auth_user_id}")
            else:
                print(f"Organization {org.get('id')} already manages agents for user {auth_user_id}")
        except Exception as exc:
            print(f"Error verifying organization agents for user {auth_user_id}: {exc}")

    def get_user_profile_by_auth_id(self, auth_user_id: str) -> Optional[Dict[str, Any]]:
        """Get user profile by auth user ID."""
        try:
            result = self.client.table('user_profiles').select('*').eq('auth_user_id', auth_user_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error getting user profile: {e}")
            return None

    def get_user_context(self, auth_user_id: str) -> Optional[UserContext]:
        """Assemble a user context using Supabase Auth as the source of truth."""
        try:
            profile = self.get_user_profile_by_auth_id(auth_user_id) or {}

            try:
                from app.auth.manager import get_auth_manager

                auth_manager = get_auth_manager()
                auth_user = auth_manager.get_auth_user(auth_user_id)
            except Exception as auth_exc:
                print(f"Error fetching auth user for context: {auth_exc}")
                auth_user = None

            email = None
            display_name = None

            if auth_user:
                email = auth_user.get("email")
                display_name = auth_user.get("display_name")
                metadata = auth_user.get("user_metadata") or {}
                if not display_name:
                    display_name = (
                        metadata.get("full_name")
                        or metadata.get("display_name")
                        or metadata.get("name")
                    )

            if not display_name and email:
                display_name = email.split("@")[0]

            timezone = profile.get("timezone") or "UTC"

            return UserContext(
                user_id=auth_user_id,
                display_name=display_name or auth_user_id,
                email=email,
                timezone=timezone,
            )
        except Exception as exc:
            print(f"Error getting user context: {exc}")
            return None

    def get_auth_user_display_name(self, auth_user_id: str) -> Optional[str]:
        try:
            response = self.client.auth.admin.get_user_by_id(auth_user_id)
        except Exception as exc:
            print(f"Error fetching auth user display name: {exc}")
            return None

        if not response or not getattr(response, "user", None):
            return None

        user = response.user
        metadata = getattr(user, "user_metadata", {}) or {}
        display_name = metadata.get("full_name") or metadata.get("name")
        if display_name and display_name.strip():
            return display_name.strip()

        email = getattr(user, "email", None)
        if email and isinstance(email, str):
            return email.split("@")[0]
        return None

    def update_user_profile(self, auth_user_id: str, updates: Dict[str, Any]) -> bool:
        """Update mutable user profile fields."""
        try:
            allowed_fields = {"avatar_url", "timezone", "is_active"}
            filtered = {k: v for k, v in updates.items() if k in allowed_fields}
            if not filtered:
                return True

            filtered["updated_at"] = datetime.now(timezone.utc).isoformat()

            result = (
                self.client
                .table('user_profiles')
                .update(filtered)
                .eq('auth_user_id', auth_user_id)
                .execute()
            )
            return bool(result.data)
        except Exception as exc:
            print(f"Error updating user profile: {exc}")
            return False

    # Legacy agent-specific methods (hire/fire/toggle) have been removed. Agents are
    # automatically provisioned at the organization level and toggled through the
    # organization agent records. Callers should use get_organization_agents().

    def update_user_run_state(
        self,
        auth_user_id: str,
        process_type: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Upsert run state for the given user."""
        try:
            profile = self.get_user_profile_by_auth_id(auth_user_id)
            if not profile:
                print(f"No user profile found for {auth_user_id} when updating run state")
                return False

            table_name = 'user_run_state'
            payload = {
                'user_id': profile['id'],
                'process_type': process_type,
                'status': status,
            }

            result = (
                self.client
                .table(table_name)
                .upsert(
                    payload,
                    on_conflict="user_id,process_type",
                    returning="representation",
                )
                .execute()
            )
            if details:
                # Persist additional metadata via activity log for observability.
                self.log_user_activity(
                    auth_user_id,
                    f"run_state_{process_type}",
                    {
                        "status": status,
                        "details": details,
                    },
                )
            return bool(result.data)
        except Exception as e:
            print(f"Error updating run state: {e}")
            return False

    def get_run_state(self, auth_user_id: str, process_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get run states for a user."""
        try:
            profile = self.get_user_profile_by_auth_id(auth_user_id)
            if not profile:
                return []

            table_name = 'user_run_state'
            query = (
                self.client
                .table(table_name)
                .select('*')
                .eq('user_id', profile['id'])
            )
            if process_type:
                query = query.eq('process_type', process_type)
            result = query.order('created_at', desc=True).execute()
            return result.data or []
        except Exception as e:
            # If table doesn't exist, return empty list
            print(f"Error getting run state: {e}")
            return []

    def log_user_activity(
        self,
        auth_user_id: str,
        action_type: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Log user activity."""
        try:
            profile = self.get_user_profile_by_auth_id(auth_user_id)
            if not profile:
                return False

            payload: Dict[str, Any] = {
                "user_id": profile["id"],
                "action_type": action_type,
                "action_data": details or {},
            }

            result = (
                self.client
                .table("user_activity_log")
                .insert(payload)
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            print(f"Error logging user activity: {e}")
            return False

    def get_user_activity(self, auth_user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get user activity history."""
        try:
            profile = self.get_user_profile_by_auth_id(auth_user_id)
            if not profile:
                return []

            result = (
                self.client
                .table("user_activity_log")
                .select("*")
                .eq("user_id", profile["id"])
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            print(f"Error getting user activity: {e}")
            # Return empty list if table doesn't exist or other database error
            return []

    # ------------------------------------------------------------------
    # Settings helpers shared with higher-level modules
    # ------------------------------------------------------------------

    def get_user_settings_record(self, internal_user_id: Any) -> Optional[Dict[str, Any]]:
        """Fetch the raw settings record for a user."""
        try:
            response = (
                self.client
                .table("user_settings")
                .select("*")
                .eq("user_id", internal_user_id)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            print(f"Error loading user settings record for user {internal_user_id}: {exc}")
            return None

    def upsert_user_settings_record(
        self,
        internal_user_id: Any,
        *,
        system_settings: Optional[Dict[str, Any]] = None,
        agent_settings: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create or update the JSON settings blobs for a user."""
        payload: Dict[str, Any] = {"user_id": internal_user_id}

        if system_settings is not None:
            payload["system_settings"] = json.dumps(system_settings)

        include_agent_settings = agent_settings is not None
        if include_agent_settings:
            payload["agent_settings"] = json.dumps(agent_settings)

        def _perform_upsert(current_payload: Dict[str, Any]) -> bool:
            response = (
                self.client
                .table("user_settings")
                .upsert(
                    current_payload,
                    on_conflict="user_id",
                    returning="representation",
                )
                .execute()
            )
            _ = response  # ensure statement executes even if unused
            return True

        try:
            return _perform_upsert(payload)
        except Exception as exc:
            message = str(exc)
            if include_agent_settings and "agent_settings" in message:
                # Some environments have not rolled out the agent_settings column yet.
                trimmed_payload = dict(payload)
                trimmed_payload.pop("agent_settings", None)
                try:
                    return _perform_upsert(trimmed_payload)
                except Exception as inner_exc:
                    print(
                        "Error upserting user settings without agent_settings column "
                        f"for user {internal_user_id}: {inner_exc}"
                    )
                    return False

            print(f"Error upserting user settings for user {internal_user_id}: {exc}")
            return False

    def get_user_settings(self, auth_user_id: str) -> Dict[str, Any]:
        """Get all settings for a user (legacy key-value store)."""
        try:
            profile = self.get_user_profile_by_auth_id(auth_user_id)
            if not profile:
                return {}

            result = (
                self.client
                .table("user_settings")
                .select("*")
                .eq("user_id", profile["id"])
                .execute()
            )

            settings: Dict[str, Any] = {}
            for setting in result.data or []:
                if "setting_key" in setting and "setting_value" in setting:
                    settings[setting["setting_key"]] = setting["setting_value"]
            return settings
        except Exception as exc:
            print(f"Error getting user settings: {exc}")
            return {}

    # Organization-aware methods for multi-user SaaS
    def get_user_organization(self, auth_user_id: str) -> Optional[Dict[str, Any]]:
        """Get user's primary organization."""
        try:
            result = self.client.rpc('get_user_primary_organization', {'p_user_id': auth_user_id}).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error getting user organization: {e}")
            return None

    def get_billing_plan(self, plan_key: str) -> Optional[Dict[str, Any]]:
        try:
            result = (
                self.client.table("billing_plans")
                .select("*")
                .eq("key", plan_key)
                .limit(1)
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as exc:
            print(f"Error fetching billing plan {plan_key}: {exc}")
            return None

    def list_billing_plans(self, *, include_inactive: bool = False) -> List[Dict[str, Any]]:
        try:
            query = self.client.table("billing_plans").select("*").order("sort_order")
            if not include_inactive:
                query = query.eq("is_active", True)
            result = query.execute()
            return result.data or []
        except Exception as exc:
            print(f"Error listing billing plans: {exc}")
            return []

    def ensure_organization_subscription(self, organization_id: str, default_plan_key: str) -> Dict[str, Any]:
        existing = self.get_organization_subscription(organization_id)
        if existing:
            return existing
        payload = {
            "organization_id": organization_id,
            "plan_key": default_plan_key,
            "status": "active",
            "billing_interval": "monthly",
            "metadata": {"provider": "stripe"},
        }
        result = (
            self.client.table("organization_subscriptions")
            .upsert(payload, on_conflict="organization_id", returning="representation")
            .execute()
        )
        data = getattr(result, "data", None)
        if data:
            if isinstance(data, list):
                return data[0]
            if isinstance(data, dict):
                return data
        record = self.get_organization_subscription(organization_id)
        if record:
            return record
        return payload

    def get_organization_subscription(self, organization_id: str) -> Optional[Dict[str, Any]]:
        try:
            result = (
                self.client.table("organization_subscriptions")
                .select("*")
                .eq("organization_id", organization_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as exc:
            print(f"Error fetching subscription for organization {organization_id}: {exc}")
            return None

    def update_organization_subscription(self, organization_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            updates = dict(updates)
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            result = (
                self.client.table("organization_subscriptions")
                .update(updates)
                .eq("organization_id", organization_id)
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as exc:
            print(f"Error updating subscription for organization {organization_id}: {exc}")
            return None

    def get_subscription_by_customer_id(self, customer_id: str) -> Optional[Dict[str, Any]]:
        if not customer_id:
            return None
        try:
            result = (
                self.client.table("organization_subscriptions")
                .select("*")
                .eq("stripe_customer_id", customer_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as exc:
            print(f"Error fetching subscription by customer {customer_id}: {exc}")
            return None

    def get_subscription_by_subscription_id(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        if not subscription_id:
            return None
        try:
            result = (
                self.client.table("organization_subscriptions")
                .select("*")
                .eq("stripe_subscription_id", subscription_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as exc:
            print(f"Error fetching subscription by subscription id {subscription_id}: {exc}")
            return None

    def has_subscription_event(self, stripe_event_id: str) -> bool:
        if not stripe_event_id:
            return False
        try:
            result = (
                self.client.table("subscription_events")
                .select("id")
                .eq("stripe_event_id", stripe_event_id)
                .limit(1)
                .execute()
            )
            return bool(result and result.data)
        except Exception as exc:
            print(f"Error checking subscription event {stripe_event_id}: {exc}")
            return False

    def record_subscription_event(
        self,
        *,
        organization_id: Optional[str],
        stripe_event_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        if not stripe_event_id:
            return
        body = {
            "organization_id": organization_id,
            "stripe_event_id": stripe_event_id,
            "event_type": event_type,
            "payload": payload,
        }
        try:
            (
                self.client.table("subscription_events")
                .upsert(body, on_conflict="stripe_event_id")
                .execute()
            )
        except Exception as exc:
            print(f"Error recording subscription event {stripe_event_id}: {exc}")

    def get_organization_membership(self, auth_user_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
        try:
            result = (
                self.client.table("organization_members")
                .select("*")
                .eq("organization_id", organization_id)
                .eq("user_id", auth_user_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as exc:
            print(f"Error fetching membership for user {auth_user_id} in org {organization_id}: {exc}")
            return None

    def record_usage_event(
        self,
        *,
        organization_id: str,
        user_id: Optional[str],
        event_type: str,
        quantity: int = 1,
        cost_usd: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not user_id:
            print(f"Skipping usage event for org {organization_id}: user_id is required")
            return
        payload = {
            "organization_id": organization_id,
            "user_id": user_id,
            "event_type": event_type,
            "quantity": quantity,
            "cost_usd": cost_usd,
            "metadata": metadata or {},
        }
        try:
            self.client.table("usage_logs").insert(payload).execute()
        except Exception as exc:
            print(f"Error recording usage event for org {organization_id}: {exc}")

    def get_usage_totals(
        self,
        *,
        organization_id: str,
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> Dict[str, int]:
        try:
            query = (
                self.client.table("usage_logs")
                .select("quantity, metadata, created_at")
                .eq("organization_id", organization_id)
            )
            if start:
                query = query.gte("created_at", start.isoformat())
            if end:
                query = query.lt("created_at", end.isoformat())
            result = query.execute()
        except Exception as exc:
            print(f"Error aggregating usage for org {organization_id}: {exc}")
            return {"actions": 0, "tokens": 0}

        totals = {"actions": 0, "tokens": 0}
        for row in result.data or []:
            try:
                quantity = int(row.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0
            totals["actions"] += max(quantity, 0)
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (ValueError, TypeError):
                    metadata = {}
            tokens = metadata.get("tokens_used") or metadata.get("tokens")
            if tokens:
                try:
                    totals["tokens"] += int(tokens)
                except (TypeError, ValueError):
                    pass
        return totals

    @staticmethod
    def _normalize_chat_thread_record(record: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(record)
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        data["metadata"] = metadata

        agent_keys = data.get("agent_keys") or []
        if isinstance(agent_keys, list):
            data["agent_keys"] = [str(key) for key in agent_keys]
        else:
            data["agent_keys"] = []
        return data

    @staticmethod
    def _normalize_chat_message_record(record: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(record)
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        data["payload"] = payload
        return data

    @staticmethod
    def _normalize_pinboard_record(record: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(record)
        attachments_raw = data.get("attachments") or []
        attachments: List[Dict[str, Any]] = []
        if isinstance(attachments_raw, list):
            for entry in attachments_raw:
                if isinstance(entry, dict):
                    attachments.append(dict(entry))
        data["attachments"] = attachments

        sources_raw = data.get("sources") or []
        sources: List[Dict[str, Any]] = []
        if isinstance(sources_raw, list):
            for entry in sources_raw:
                if isinstance(entry, dict):
                    sources.append(dict(entry))
        data["sources"] = sources
        return data

    def list_chat_threads(
        self,
        auth_user_id: str,
        *,
        organization_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return chat threads for a user ordered by recent activity."""
        try:
            query = (
                self.client.table("chat_threads")
                .select("*")
                .eq("user_id", auth_user_id)
                .order("updated_at", desc=True)
                .limit(limit)
            )
            if organization_id:
                query = query.eq("organization_id", organization_id)
            result = query.execute()
            return [self._normalize_chat_thread_record(row) for row in (result.data or [])]
        except Exception as exc:
            print(f"Error listing chat threads: {exc}")
            return []

    def get_chat_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a chat thread by id."""
        try:
            result = (
                self.client.table("chat_threads")
                .select("*")
                .eq("id", thread_id)
                .limit(1)
                .execute()
            )
            if not result.data:
                return None
            return self._normalize_chat_thread_record(result.data[0])
        except Exception as exc:
            print(f"Error fetching chat thread {thread_id}: {exc}")
            return None

    def create_chat_thread(
        self,
        *,
        auth_user_id: str,
        organization_id: Optional[str],
        title: str,
        kind: str,
        agent_keys: Sequence[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new chat thread."""
        payload: Dict[str, Any] = {
            "user_id": auth_user_id,
            "title": title,
            "kind": kind,
            "agent_keys": list(agent_keys),
            "metadata": metadata or {},
        }
        if organization_id:
            payload["organization_id"] = organization_id
        try:
            result = self.client.table("chat_threads").insert(payload).execute()
            if not result.data:
                return None
            return self._normalize_chat_thread_record(result.data[0])
        except Exception as exc:
            print(f"Error creating chat thread: {exc}")
            return None

    def update_chat_thread(
        self,
        thread_id: str,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update an existing chat thread."""
        try:
            result = (
                self.client.table("chat_threads")
                .update(updates)
                .eq("id", thread_id)
                .execute()
            )
            if not result.data:
                return None
            return self._normalize_chat_thread_record(result.data[0])
        except Exception as exc:
            print(f"Error updating chat thread {thread_id}: {exc}")
            return None

    def touch_chat_thread(self, thread_id: str) -> bool:
        """Update the thread's last activity timestamp."""
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            self.client.table("chat_threads").update({"updated_at": timestamp}).eq("id", thread_id).execute()
            return True
        except Exception as exc:
            print(f"Error touching chat thread {thread_id}: {exc}")
            return False

    def delete_chat_thread(self, thread_id: str, auth_user_id: str) -> bool:
        """Delete a chat thread owned by the specified user."""
        try:
            result = (
                self.client.table("chat_threads")
                .delete()
                .eq("id", thread_id)
                .eq("user_id", auth_user_id)
                .execute()
            )
            if result is None:
                return False
            data = getattr(result, "data", None)
            if data is None:
                # Supabase returns None when returning="minimal"; assume success
                return True
            return len(data) > 0
        except Exception as exc:
            print(f"Error deleting chat thread {thread_id}: {exc}")
            return False

    def clear_chat_messages(self, thread_id: str, auth_user_id: str) -> bool:
        """Clear all messages from a chat thread owned by the specified user."""
        try:
            # First verify thread ownership
            thread = self.get_chat_thread(thread_id)
            if not thread or str(thread.get("user_id")) != auth_user_id:
                return False
            
            # Delete all messages from the thread
            result = (
                self.client.table("chat_messages")
                .delete()
                .eq("thread_id", thread_id)
                .execute()
            )
            
            # Touch the thread to update its timestamp
            self.touch_chat_thread(thread_id)
            
            return True
        except Exception as exc:
            print(f"Error clearing chat messages for thread {thread_id}: {exc}")
            return False

    def list_chat_messages(
        self,
        thread_id: str,
        *,
        limit: int = 200,
        ascending: bool = True,
    ) -> List[Dict[str, Any]]:
        """Fetch messages for a thread."""
        try:
            result = (
                self.client.table("chat_messages")
                .select("*")
                .eq("thread_id", thread_id)
                .order("created_at", desc=not ascending)
                .limit(limit)
                .execute()
            )
            records = result.data or []
            return [self._normalize_chat_message_record(row) for row in records]
        except Exception as exc:
            print(f"Error listing chat messages for thread {thread_id}: {exc}")
            return []

    def insert_chat_message(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        author: Optional[str],
        payload: Optional[Dict[str, Any]],
        organization_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Insert a message into a chat thread."""
        entry: Dict[str, Any] = {
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "author": author,
            "payload": payload or {},
        }
        if organization_id:
            entry["organization_id"] = organization_id
        if user_id:
            entry["user_id"] = user_id
        try:
            result = self.client.table("chat_messages").insert(entry).execute()
            if not result.data:
                return None
            return self._normalize_chat_message_record(result.data[0])
        except Exception as exc:
            print(f"Error inserting chat message for thread {thread_id}: {exc}")
            return None

    def list_pinboard_posts(
        self,
        *,
        organization_id: Optional[str],
        user_id: Optional[str],
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List pinboard posts scoped to an organization or user."""
        try:
            query = self.client.table("pinboard_posts").select("*").order("created_at", desc=True).limit(limit)
            if organization_id:
                query = query.eq("organization_id", organization_id)
            elif user_id:
                query = query.eq("user_id", user_id)
            result = query.execute()
            return [self._normalize_pinboard_record(row) for row in (result.data or [])]
        except Exception as exc:
            print(f"Error listing pinboard posts: {exc}")
            return []

    def get_pinboard_post_by_slug(
        self,
        *,
        organization_id: Optional[str],
        slug: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a pinboard post by slug."""
        try:
            query = (
                self.client.table("pinboard_posts")
                .select("*")
                .eq("slug", slug)
                .limit(1)
            )
            if organization_id:
                query = query.eq("organization_id", organization_id)
            result = query.execute()
            if not result.data:
                return None
            return self._normalize_pinboard_record(result.data[0])
        except Exception as exc:
            print(f"Error fetching pinboard post ({slug}): {exc}")
            return None

    def create_pinboard_post(
        self,
        *,
        organization_id: Optional[str],
        user_id: Optional[str],
        author_agent_key: str,
        title: str,
        slug: str,
        content_md: str,
        excerpt: Optional[str],
        cover_url: Optional[str],
        attachments: Optional[Sequence[Dict[str, Any]]],
        sources: Optional[Sequence[Dict[str, Any]]],
        priority: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Create a new pinboard post."""
        payload: Dict[str, Any] = {
            "author_agent_key": author_agent_key,
            "title": title,
            "slug": slug,
            "content_md": content_md,
            "attachments": list(attachments or []),
            "sources": list(sources or []),
        }
        if priority:
            payload["priority"] = priority
        if excerpt is not None:
            payload["excerpt"] = excerpt
        if cover_url:
            payload["cover_url"] = cover_url
        if organization_id:
            payload["organization_id"] = organization_id
        if user_id:
            payload["user_id"] = user_id
        try:
            result = self.client.table("pinboard_posts").insert(payload).execute()
            if not result.data:
                return None
            return self._normalize_pinboard_record(result.data[0])
        except Exception as exc:
            print(f"Error creating pinboard post: {exc}")
            return None

    def delete_pinboard_post(
        self,
        *,
        post_id: str,
        organization_id: Optional[str],
        user_id: Optional[str],
    ) -> bool:
        try:
            query = self.client.table("pinboard_posts").delete().eq("id", post_id)
            if organization_id:
                query = query.eq("organization_id", organization_id)
            if user_id:
                query = query.eq("user_id", user_id)
            result = query.execute()
            if result is None:
                return False
            data = getattr(result, "data", None)
            if data is None:
                return True
            return len(data) > 0
        except Exception as exc:
            print(f"Error deleting pinboard post {post_id}: {exc}")
            return False

    def get_organization_agents(self, org_id: str) -> List[Dict[str, Any]]:
        """Get agents for an organization."""
        try:
            result = self.client.table('agents').select('*').eq('organization_id', org_id).eq('is_active', True).execute()
            return result.data or []
        except Exception as e:
            print(f"Error getting organization agents: {e}")
            return []

    def add_agent_to_organization(self, org_id: str, agent_key: str, agent_data: Dict[str, Any], created_by: str) -> bool:
        """Add an agent to an organization."""
        try:
            # First check if agent already exists
            existing = self.client.table('agents').select('*').eq('organization_id', org_id).eq('key', agent_key).execute()
            
            if existing.data:
                # Reactivate if exists but inactive
                if not existing.data[0]['is_active']:
                    result = self.client.table('agents').update({
                        'is_active': True,
                        'updated_at': datetime.now().isoformat()
                    }).eq('id', existing.data[0]['id']).execute()
                    return result.data is not None
                return True  # Already exists and active
            
            # Create new agent
            result = self.client.table('agents').insert({
                'organization_id': org_id,
                'key': agent_key,
                'name': agent_data.get('name', agent_key.title()),
                'description': agent_data.get('description', ''),
                'agent_type': agent_data.get('agent_type', 'assistant'),
                'config': agent_data.get('config', {}),
                'is_active': True,
                'created_by': created_by
            }).execute()
            
            return result.data is not None
        except Exception as e:
            print(f"Error adding agent to organization: {e}")
            return False

    def remove_agent_from_organization(self, org_id: str, agent_key: str) -> bool:
        """Remove an agent from an organization (soft delete by setting is_active to False)."""
        try:
            result = self.client.table('agents').update({
                'is_active': False,
                'updated_at': datetime.now().isoformat()
            }).eq('organization_id', org_id).eq('key', agent_key).execute()
            
            return result.data is not None
        except Exception as e:
            print(f"Error removing agent from organization: {e}")
            return False

    def get_agent_by_key(self, org_id: str, agent_key: str) -> Optional[Dict[str, Any]]:
        """Get a specific agent by key for an organization."""
        try:
            result = self.client.table('agents').select('*').eq('organization_id', org_id).eq('key', agent_key).eq('is_active', True).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error getting agent by key: {e}")
            return None

    def upsert_agent_catalog_agent(self, payload: Dict[str, Any]) -> bool:
        """Create or update a catalog agent definition."""

        try:
            self.client.table("agent_catalog_agents").upsert(payload, on_conflict="key").execute()
            return True
        except Exception as exc:
            print(f"Error upserting agent catalog agent {payload.get('key')}: {exc}")
            return False

    def upsert_agent_catalog_version(self, payload: Dict[str, Any]) -> bool:
        """Create or update a catalog version record."""

        try:
            self.client.table("agent_catalog_versions").upsert(payload, on_conflict="agent_key,version").execute()
            return True
        except Exception as exc:
            print(
                f"Error upserting agent catalog version {payload.get('agent_key')}@{payload.get('version')}: {exc}"
            )
            return False

    def list_agent_catalog_agents(
        self,
        *,
        environment: str,
        organization_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return published catalog agents."""

        try:
            query = (
                self.client.table("agent_catalog_versions")
                .select(
                    "agent_key, version, status, manifest_snapshot, bundle_url, bundle_checksum, bundle_signature, signature_algorithm, published_at, "
                    "agent:agent_catalog_agents(key, display_name, description, icon_url, category)"
                )
                .eq("status", "published")
                .order("agent_key", desc=False)
                .order("published_at", desc=True)
            )
            result = query.execute()
        except Exception as exc:
            print(f"Error fetching agent catalog: {exc}")
            return []

        # Group by agent_key to get the latest published version for each agent
        latest_versions = {}
        for row in result.data or []:
            agent_key = row.get("agent_key")
            if agent_key not in latest_versions:
                latest_versions[agent_key] = row

        entries: List[Dict[str, Any]] = []
        for row in latest_versions.values():
            agent_block = row.get("agent") or {}
            manifest_snapshot = row.get("manifest_snapshot")
            if isinstance(manifest_snapshot, str):
                try:
                    manifest_snapshot = json.loads(manifest_snapshot)
                except (TypeError, ValueError, json.JSONDecodeError):
                    manifest_snapshot = None

            # Extract audience information from manifest if available
            audience_block = None
            if manifest_snapshot and isinstance(manifest_snapshot, dict):
                audience_block = manifest_snapshot.get("audience")

            # Check organization allowlist if specified
            if organization_id and audience_block:
                allow_list = audience_block.get("organization_allowlist") or audience_block.get("org_allow_list")
                if isinstance(allow_list, list) and allow_list and organization_id not in allow_list:
                    continue

            entry = {
                "key": row.get("agent_key"),
                "name": agent_block.get("display_name") or row.get("agent_key"),
                "description": agent_block.get("description"),
                "icon": agent_block.get("icon_url"),
                "category": agent_block.get("category"),
                "version": row.get("version"),
                "status": row.get("status"),
                "audience": audience_block,
                "manifest": manifest_snapshot,
                "bundle_url": row.get("bundle_url"),
                "bundle_checksum": row.get("bundle_checksum"),
                "bundle_signature": row.get("bundle_signature"),
                "signature_algorithm": row.get("signature_algorithm"),
                "published_at": row.get("published_at"),
            }
            entries.append(entry)

        return sorted(entries, key=lambda item: (item.get("name") or "", item.get("key") or ""))

    def get_agent_catalog_entry(
        self,
        *,
        agent_key: str,
        environment: str,
        organization_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the active catalog entry for a single agent, if available."""

        normalized = (agent_key or "").strip().casefold()
        if not normalized:
            return None

        entries = self.list_agent_catalog_agents(environment=environment, organization_id=organization_id)
        for entry in entries:
            key = (entry.get("key") or "").strip().casefold()
            if key == normalized:
                return entry
        return None

    def create_task(self, org_id: str, task_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a task for an organization."""
        try:
            result = self.client.rpc('create_organization_task', {
                'p_organization_id': org_id,
                'p_name': task_data.get('name', f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"),
                'p_title': task_data.get('title'),
                'p_description': task_data.get('description'),
                'p_agent_id': task_data.get('agent_id'),
                'p_details': task_data.get('details', {}),
                'p_created_by': task_data.get('created_by')
            }).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error creating task: {e}")
            return None

    def get_organization_tasks(self, org_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Get tasks for an organization."""
        try:
            query = self.client.table('tasks').select('*').eq('organization_id', org_id)
            
            if filters:
                if 'status' in filters:
                    query = query.eq('status', filters['status'])
                if 'agent_id' in filters:
                    query = query.eq('agent_id', filters['agent_id'])
                if 'limit' in filters:
                    query = query.limit(filters['limit'])
            
            result = query.order('created_at', desc=True).execute()
            return result.data or []
        except Exception as e:
            print(f"Error getting organization tasks: {e}")
            return []

    def log_run_start(self, org_id: str, task_id: Optional[str], agent_id: Optional[str], 
                     user_id: Optional[str]) -> Optional[str]:
        """Log the start of a run."""
        try:
            result = self.client.rpc('log_organization_run_start', {
                'p_organization_id': org_id,
                'p_task_id': task_id,
                'p_agent_id': agent_id,
                'p_input': None,
                'p_created_by': user_id
            }).execute()
            return result.data if result.data else None
        except Exception as e:
            print(f"Error logging run start: {e}")
            return None

    def log_run_complete(self, run_id: str, result: Dict[str, Any]) -> bool:
        """Log the completion of a run."""
        try:
            self.client.rpc('complete_organization_run', {
                'p_run_id': run_id,
                'p_status': result.get('status', 'completed'),
                'p_output': result.get('output'),
                'p_error_message': result.get('error_message'),
                'p_tokens_used': result.get('tokens_used', 0),
                'p_duration_ms': result.get('duration_ms'),
                'p_cost_usd': result.get('cost')
            }).execute()
            return True
        except Exception as e:
            print(f"Error logging run completion: {e}")
            return False

    def get_organization_runs(self, org_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent runs for an organization."""
        try:
            result = self.client.table('runs').select('*').eq('organization_id', org_id).order('created_at', desc=True).limit(limit).execute()
            return result.data or []
        except Exception as e:
            print(f"Error getting organization runs: {e}")
            return []

    # ------------------------------------------------------------------
    # Agent job management
    # ------------------------------------------------------------------
    def _normalize_job_record(self, record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not record:
            return None
        normalised = dict(record)
        for key in ("payload", "result", "error", "metadata"):
            value = normalised.get(key)
            if isinstance(value, str):
                try:
                    normalised[key] = json.loads(value)
                except (TypeError, ValueError):
                    pass
        return normalised

    def create_agent_job(
        self,
        auth_user_id: str,
        agent_key: str,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": job_id,
            "auth_user_id": auth_user_id,
            "agent_key": agent_key,
            "status": "queued",
            "payload": payload or {},
            "metadata": metadata or {},
            "result": None,
            "error": None,
            "progress": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }

        try:
            result = (
                self.client.table("agent_jobs")
                .insert(record)
                .execute()
            )
            if result and result.data:
                return self._normalize_job_record(result.data[0])
        except Exception as exc:
            print(f"Error creating agent job for {auth_user_id}: {exc}")

        return self._normalize_job_record(record) or record

    def update_agent_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
        progress: Optional[float] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        updates: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}

        if status is not None:
            updates["status"] = status
        if result is not None:
            updates["result"] = result
        if error is not None:
            updates["error"] = error
        if progress is not None:
            updates["progress"] = progress
        if metadata is not None:
            updates["metadata"] = metadata
        if started_at is not None:
            updates["started_at"] = started_at.isoformat() if isinstance(started_at, datetime) else started_at
        if finished_at is not None:
            updates["finished_at"] = finished_at.isoformat() if isinstance(finished_at, datetime) else finished_at

        if len(updates) == 1:  # only updated_at
            return self.get_agent_job(job_id)

        try:
            self.client.table("agent_jobs").update(updates).eq("id", job_id).execute()
        except Exception as exc:
            print(f"Error updating agent job {job_id}: {exc}")
            return None

        return self.get_agent_job(job_id)

    def get_agent_job(self, job_id: str, auth_user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            query = (
                self.client.table("agent_jobs")
                .select("*")
                .eq("id", job_id)
                .limit(1)
            )
            if auth_user_id:
                query = query.eq("auth_user_id", auth_user_id)
            result = query.execute()
            if result and result.data:
                return self._normalize_job_record(result.data[0])
        except Exception as exc:
            print(f"Error fetching agent job {job_id}: {exc}")
        return None

    def list_agent_jobs(self, auth_user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            result = (
                self.client.table("agent_jobs")
                .select("*")
                .eq("auth_user_id", auth_user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = result.data or []
            return [self._normalize_job_record(row) for row in rows]
        except Exception as exc:
            print(f"Error listing agent jobs for {auth_user_id}: {exc}")
            return []

    def get_active_agent_job_for_thread(
        self,
        auth_user_id: str,
        thread_id: str,
        agent_key: Optional[str] = None,
        limit: int = 10,
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent active job for the given thread and optional agent."""

        try:
            query = (
                self.client.table("agent_jobs")
                .select("*")
                .eq("auth_user_id", auth_user_id)
                .filter("metadata->>thread_id", "eq", thread_id)
                .order("created_at", desc=True)
                .limit(limit)
            )
            if agent_key:
                query = query.eq("agent_key", agent_key)

            result = query.execute()
            rows = result.data or []
        except Exception as exc:
            print(f"Error fetching active job for thread {thread_id}: {exc}")
            return None

        for row in rows:
            record = self._normalize_job_record(row)
            status = str((record or {}).get("status", "")).lower()
            if status in {"queued", "running", "cancelling"}:
                return record

        return None


# Global database client instance
_database_client: Optional[object] = None


def get_database_client():
    """Get the global database client instance."""
    global _database_client
    if _database_client is None:
        # Ensure environment is loaded
        from dotenv import load_dotenv

        load_dotenv()

        _database_client = SupabaseDatabaseClient()
    return _database_client


# Simple alias for readability
DatabaseClient = SupabaseDatabaseClient


def initialize_database():
    """Initialize database connection and verify setup."""
    try:
        client = get_database_client()
        print("Database client initialized successfully")
        return True
    except Exception as e:
        print(f"Failed to initialize database: {e}")
        return False
