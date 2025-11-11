"""
User Context Management for Multi-User Operations

This module provides the UserContext dataclass and utilities for managing
user-specific configuration, credentials, and resource isolation across
the automation suite.
"""

import os
import json
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from pathlib import Path
from cryptography.fernet import Fernet

if TYPE_CHECKING:  # pragma: no cover - type checking helper
    from app.billing.plans import PlanContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class UserContext:
    """
    Contains all user-specific information needed to run agents and operations.
    
    This context is injected into all agents, utilities, and services to ensure
    proper isolation and user-specific configuration.
    """
    user_id: str
    display_name: str
    email: Optional[str]
    enabled_agents: List[str] = None
    agent_configs: Dict[str, Any] = None
    timezone: str = "UTC"
    plan_context: Optional['PlanContext'] = None
    
    def __post_init__(self):
        """Initialize default values after dataclass creation."""
        if self.enabled_agents is None:
            self.enabled_agents = []
        if self.agent_configs is None:
            self.agent_configs = {}
    
    def get_download_dir(self) -> str:
        """Get user-specific files directory path."""
        user_dir = PROJECT_ROOT / "files" / "users" / self.user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return str(user_dir)
    
    def get_temp_dir(self) -> str:
        """Get user-specific temporary directory path."""
        temp_dir = PROJECT_ROOT / "temp" / "users" / self.user_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        return str(temp_dir)
    
    def get_logs_dir(self) -> str:
        """Get user-specific logs directory path."""
        logs_dir = PROJECT_ROOT / "logs" / "users" / self.user_id
        logs_dir.mkdir(parents=True, exist_ok=True)
        return str(logs_dir)
    
    def get_config(self):
        """
        Generate a Settings object with user-specific configuration.
        
        This replaces the global config with user-scoped values.
        """
        from . import settings  # Import here to avoid circular imports
        
        config = settings.Settings()
        config.user_display_name = self.display_name
        config.user_email = self.email
        config.user_timezone = self.timezone
        
        return config
    
    def is_agent_enabled(self, agent_key: str) -> bool:
        """Check if a specific agent is enabled for this user."""
        return agent_key in self.enabled_agents
    
    def get_agent_config(self, agent_key: str) -> Dict[str, Any]:
        """Get configuration for a specific agent."""
        return self.agent_configs.get(agent_key, {})
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert UserContext to dictionary (for serialization)."""
        data = {
            'user_id': self.user_id,
            'display_name': self.display_name,
            'email': self.email,
            'enabled_agents': self.enabled_agents,
            'agent_configs': self.agent_configs,
            'timezone': self.timezone,
        }
        if self.plan_context is not None:
            data['plan_context'] = self.plan_context.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserContext':
        """Create UserContext from dictionary."""
        return cls(**data)
    
    @classmethod
    def from_database(cls, auth_user_id: str) -> Optional['UserContext']:
        """
        Load user context from database by auth user ID.
        
        This is the primary method for loading user context in the web interface
        and supervisor processes.
        """
        try:
            from app.db import get_database_client

            db = get_database_client()
            return db.get_user_context(auth_user_id)
        except ImportError:
            # Database module not available - fall back to environment/file loading
            return None
        except Exception as e:
            print(f"Error loading user context from database: {e}")
            return None


def _discover_default_agent_keys() -> List[str]:
    try:
        from app.registry.agents.store import AgentStore
        agent_store = AgentStore()
        agents = agent_store.get_available_agents()
        return [agent.key for agent in agents]
    except Exception:
        return []  # Return empty list if discovery fails


def _json_env(name: str, fallback_value: Any) -> Any:
    raw = os.getenv(name)
    if raw is None:
        if isinstance(fallback_value, dict):
            return dict(fallback_value)
        if isinstance(fallback_value, list):
            return list(fallback_value)
        return fallback_value
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if isinstance(fallback_value, dict):
            return dict(fallback_value)
        if isinstance(fallback_value, list):
            return list(fallback_value)
        return fallback_value


def load_user_context_from_env() -> Optional[UserContext]:
    """
    Load user context from environment variables.
    
    This is used when running agents as separate processes with
    user-specific environment variables set by the supervisor.
    """
    user_id = os.getenv('USER_ID')
    if not user_id:
        return None
    
    default_agents = _discover_default_agent_keys()

    return UserContext(
        user_id=user_id,
        display_name=os.getenv('USER_DISPLAY_NAME', 'Unknown User'),
        email=os.getenv('USER_EMAIL'),
        enabled_agents=_json_env('ENABLED_AGENTS', default_agents),
        agent_configs=_json_env('AGENT_CONFIGS', {}),
        timezone=os.getenv('USER_TIMEZONE', 'UTC')
    )


def load_user_context_from_file(file_path: str) -> Optional[UserContext]:
    """
    Load user context from a JSON file.
    
    This is used for CLI usage or local development.
    """
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        return UserContext.from_dict(data)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def get_default_user_context() -> UserContext:
    """
    Get a default user context for backwards compatibility.
    
    This loads from the existing global configuration and environment
    variables to maintain compatibility with single-user setups.
    """
    from .. import config
    
    default_agents = _discover_default_agent_keys()

    # Try to load from current config
    try:
        return UserContext(
            user_id=os.getenv('USER_ID', 'default'),
            display_name=getattr(config, 'USER_DISPLAY_NAME', 'Default User'),
            email=getattr(config, 'USER_EMAIL', None),
            enabled_agents=default_agents,
            timezone='UTC'
        )
    except Exception:
        # Fallback minimal context
        return UserContext(
            user_id='default',
            display_name='Default User',
            email=None,
            enabled_agents=default_agents
        )


# Encryption utilities for secure token storage
def get_encryption_key() -> bytes:
    """
    Get or generate encryption key for storing sensitive data.
    
    In production, this should come from a secure key management system.
    For now, we'll use an environment variable or generate one.
    """
    key = os.getenv('ENCRYPTION_KEY')
    if key:
        return key.encode()
    
    # Generate a new key (this should be saved securely in production)
    new_key = Fernet.generate_key()
    print(f"Generated new encryption key. Set ENCRYPTION_KEY environment variable to: {new_key.decode()}")
    return new_key


def encrypt_token(token: str) -> str:
    """Encrypt a sensitive token for storage."""
    if not token:
        return ""
    
    key = get_encryption_key()
    fernet = Fernet(key)
    encrypted_token = fernet.encrypt(token.encode())
    return encrypted_token.decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a stored token."""
    if not encrypted_token:
        return ""
    
    key = get_encryption_key()
    fernet = Fernet(key)
    try:
        decrypted_token = fernet.decrypt(encrypted_token.encode())
        return decrypted_token.decode()
    except Exception as e:
        print(f"Failed to decrypt token: {e}")
        return ""
