"""
Database models and schema definitions for the multi-user automation system.

This module contains data classes and type definitions for database entities.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class UserProfile:
    """User profile data structure."""
    auth_user_id: str
    avatar_url: Optional[str]
    timezone: str = "UTC"
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class AgentSettings:
    """Agent configuration settings structure."""
    user_id: str
    agent_key: str
    settings: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class RunRecord:
    """Agent run tracking record."""
    user_id: str
    agent_key: str
    status: str
    metadata: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None