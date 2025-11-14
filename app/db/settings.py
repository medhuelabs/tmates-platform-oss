"""
JSON-based settings loader for multi-agent system.

This module provides functionality to load configuration settings from JSON columns
in the database, supporting both system-wide settings and agent-specific settings
without requiring database migrations for new agents or settings.
"""

from __future__ import annotations

import os
import json
import importlib
from typing import Any, Dict, Optional, Tuple
from .client import get_database_client
from app.logger import log

MOBILE_SETTINGS_KEY = "mobile_preferences"

DEFAULT_MOBILE_SETTINGS: Dict[str, Any] = {
    "allow_notifications": True,
    "mentions": True,
    "direct_messages": True,
    "team_messages": True,
    "usage_analytics": True,
    "crash_reports": True,
    "theme_preference": "system",
}

_VALID_THEME_PREFERENCES = {"system", "light", "dark"}


def _safe_log(*parts: Any, verbose: bool = False) -> None:
    """Log messages with optional verbose filtering."""
    message = " ".join(str(p) for p in parts)
    if not message:
        return
    try:
        log(message, agent="system", feed=False)
    except Exception:
        # Fallback to direct stdout if logging helpers are unavailable.
        print(message, flush=True)


def _deserialize_settings(value: Any) -> Dict[str, Any]:
    """Normalize JSON/text payloads from the database into dicts."""
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def get_system_defaults() -> Dict[str, Any]:
    """Get default system settings."""
    return {
        # General agent behavior
        "SUPPRESS_SYSTEM_LOGS": True,
        "FEED_LOGGING_ENABLED": False,
        
        # User info
        "USER_DISPLAY_NAME": "Andrew",
        "USER_EMAIL": "",
        MOBILE_SETTINGS_KEY: DEFAULT_MOBILE_SETTINGS.copy(),
    }


def get_agent_defaults(agent_name: str) -> Dict[str, Any]:
    """
    Get default settings for a specific agent by loading its settings module.
    
    Args:
        agent_name: Name of the agent (e.g., 'adam')
        
    Returns:
        Dictionary of default settings for the agent
    """
    try:
        # Import the agent's settings module
        settings_module = importlib.import_module(f"agents.{agent_name}.settings")
        
        # Get defaults from the module
        if hasattr(settings_module, 'get_default_settings'):
            return settings_module.get_default_settings()
        elif hasattr(settings_module, 'DEFAULT_SETTINGS'):
            return settings_module.DEFAULT_SETTINGS.copy()
        else:
            _safe_log(f"[settings] No default settings found for agent {agent_name}")
            return {}
            
    except ImportError:
        _safe_log(f"[settings] No settings module found for agent {agent_name}")
        return {}
    except Exception as e:
        _safe_log(f"[settings] Error loading defaults for agent {agent_name}: {e}")
        return {}


def load_user_system_settings(user_id: str) -> Dict[str, Any]:
    """
    Load system settings for a user from the database.
    
    Args:
        user_id: Auth user ID (will be converted to internal user ID)
    
    Returns defaults merged with user customizations.
    """
    try:
        db = get_database_client()

        profile = db.get_user_profile_by_auth_id(user_id)
        if not profile:
            _safe_log(f"[settings] user profile not found for auth ID {user_id}")
            return get_system_defaults()

        internal_user_id = profile["id"]
        record = db.get_user_settings_record(internal_user_id)

        user_settings = _deserialize_settings(record.get("system_settings")) if record else {}

        defaults = get_system_defaults()
        merged_settings = {**defaults, **user_settings}

        _safe_log(f"[settings] loaded {len(user_settings)} system customizations for user {user_id}")
        return merged_settings

    except Exception as exc:
        _safe_log(f"[settings] failed to load system settings for user {user_id}: {exc}")
        return get_system_defaults()


def load_user_agent_settings(user_id: str, agent_name: str) -> Dict[str, Any]:
    """
    Load settings for a specific agent for a user.
    
    Args:
        user_id: Auth user ID (will be converted to internal user ID)
        agent_name: Name of the agent
    
    Returns agent defaults merged with user customizations.
    """
    try:
        db = get_database_client()

        profile = db.get_user_profile_by_auth_id(user_id)
        if not profile:
            _safe_log(f"[settings] user profile not found for auth ID {user_id}")
            return get_agent_defaults(agent_name)

        internal_user_id = profile["id"]
        record = db.get_user_settings_record(internal_user_id)

        agent_settings_map = _deserialize_settings(record.get("agent_settings")) if record else {}
        user_agent_settings = _deserialize_settings(agent_settings_map.get(agent_name)) if agent_settings_map else {}

        defaults = get_agent_defaults(agent_name)
        merged_settings = {**defaults, **user_agent_settings}

        _safe_log(f"[settings] loaded {len(user_agent_settings)} customizations for agent {agent_name}, user {user_id}")
        return merged_settings

    except Exception as exc:
        _safe_log(f"[settings] failed to load {agent_name} settings for user {user_id}: {exc}")
        return get_agent_defaults(agent_name)


def save_user_system_settings(user_id: str, settings: Dict[str, Any]) -> bool:
    """Save system settings for a user."""
    try:
        db = get_database_client()
        
        # Convert auth user ID to internal user ID
        profile = db.get_user_profile_by_auth_id(user_id)
        if not profile:
            _safe_log(f"[settings] user profile not found for auth ID {user_id}")
            return False
        
        internal_user_id = profile['id']

        current_record = db.get_user_settings_record(internal_user_id) or {}
        agent_settings = _deserialize_settings(current_record.get("agent_settings"))

        success = db.upsert_user_settings_record(
            internal_user_id,
            system_settings=settings,
            agent_settings=agent_settings,
        )

        if success:
            _safe_log(f"[settings] saved {len(settings)} system settings for user {user_id}")
        return success
        
    except Exception as exc:
        _safe_log(f"[settings] failed to save system settings for user {user_id}: {exc}")
        return False


def save_user_agent_settings(user_id: str, agent_name: str, settings: Dict[str, Any]) -> bool:
    """Save settings for a specific agent for a user."""
    try:
        db = get_database_client()
        
        # Convert auth user ID to internal user ID
        profile = db.get_user_profile_by_auth_id(user_id)
        if not profile:
            _safe_log(f"[settings] user profile not found for auth ID {user_id}")
            return False
        
        internal_user_id = profile['id']
        
        current_record = db.get_user_settings_record(internal_user_id) or {}
        system_settings = _deserialize_settings(current_record.get("system_settings"))
        all_agent_settings = _deserialize_settings(current_record.get("agent_settings"))

        all_agent_settings[agent_name] = settings

        success = db.upsert_user_settings_record(
            internal_user_id,
            system_settings=system_settings,
            agent_settings=all_agent_settings,
        )

        if success:
            _safe_log(f"[settings] saved {len(settings)} settings for agent {agent_name}, user {user_id}")
        return success
        
    except Exception as exc:
        _safe_log(f"[settings] failed to save {agent_name} settings for user {user_id}: {exc}")
        return False


def apply_system_settings_to_config(config_obj: Any, user_id: str) -> int:
    """
    Apply user's system settings to a config object.
    
    This maintains compatibility with the existing config system.
    """
    system_settings = load_user_system_settings(user_id)
    
    applied_count = 0
    
    # Map system setting names to config attribute names
    attribute_mappings = {
        "SUPPRESS_SYSTEM_LOGS": "suppress_system_logs",
        "USER_DISPLAY_NAME": "user_display_name",
        "USER_EMAIL": "user_email",
    }
    
    for setting_name, setting_value in system_settings.items():
        # Skip None or empty setting names
        if not setting_name:
            continue
            
        # Skip if environment variable override exists
        env_name = setting_name.upper()
        if os.getenv(env_name) is not None:
            _safe_log(f"[settings] skipping {setting_name} (env override {env_name} exists)", verbose=True)
            continue

        # Skip complex values that are reserved for client preferences
        if isinstance(setting_value, (dict, list)):
            continue
        
        # Get config attribute name
        attr_name = attribute_mappings.get(setting_name, setting_name.lower() if setting_name else "")
        
        # Apply to config object if attribute exists
        if hasattr(config_obj, attr_name):
            try:
                setattr(config_obj, attr_name, setting_value)
                applied_count += 1
                _safe_log(f"[settings] applied setting: {attr_name} = {setting_value}", verbose=True)
            except Exception as exc:
                _safe_log(f"[settings] failed to apply setting {attr_name}: {exc}")
        else:
            _safe_log(f"[settings] unknown config attribute: {attr_name}", verbose=True)
    
    return applied_count


# Backward compatibility functions
def load_user_settings(user_id: str) -> Dict[str, Any]:
    """Load user settings (backward compatibility)."""
    return load_user_system_settings(user_id)


def apply_user_settings(config_obj: Any, user_id: str) -> int:
    """Apply user settings to config (backward compatibility)."""
    return apply_system_settings_to_config(config_obj, user_id)


def _sanitize_mobile_settings(raw_settings: Any) -> Dict[str, Any]:
    """Return a sanitized mobile settings payload with defaults applied."""

    sanitized = DEFAULT_MOBILE_SETTINGS.copy()
    if not isinstance(raw_settings, dict):
        return sanitized

    for key, value in raw_settings.items():
        if key not in sanitized:
            continue

        if key == "theme_preference":
            if isinstance(value, str) and value in _VALID_THEME_PREFERENCES:
                sanitized[key] = value
            continue

        if isinstance(value, bool):
            sanitized[key] = value

    return sanitized


def _filter_mobile_settings_update(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Filter incoming update payload to recognised mobile settings keys."""

    permitted: Dict[str, Any] = {}
    for key, value in payload.items():
        if key not in DEFAULT_MOBILE_SETTINGS:
            continue

        if key == "theme_preference":
            if isinstance(value, str) and value in _VALID_THEME_PREFERENCES:
                permitted[key] = value
            continue

        if isinstance(value, bool):
            permitted[key] = value

    return permitted


def load_user_mobile_settings(user_id: str) -> Dict[str, Any]:
    """Load mobile preferences for a user, applying defaults and sanitisation."""

    system_settings = load_user_system_settings(user_id)
    raw_settings = system_settings.get(MOBILE_SETTINGS_KEY, {}) if isinstance(system_settings, dict) else {}
    return _sanitize_mobile_settings(raw_settings)


def save_user_mobile_settings(user_id: str, updates: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """Persist mobile preference updates and return the resulting settings."""

    filtered_updates = _filter_mobile_settings_update(updates or {})
    current_system_settings = load_user_system_settings(user_id)
    if not isinstance(current_system_settings, dict):
        current_system_settings = {}

    current_mobile_settings = _sanitize_mobile_settings(
        current_system_settings.get(MOBILE_SETTINGS_KEY, {})
    )

    if filtered_updates:
        current_mobile_settings.update(filtered_updates)

    current_system_settings[MOBILE_SETTINGS_KEY] = current_mobile_settings
    success = save_user_system_settings(user_id, current_system_settings)
    return success, current_mobile_settings.copy()
