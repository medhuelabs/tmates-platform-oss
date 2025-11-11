"""
Database module for multi-user automation system.

This module provides database functionality including:
- Supabase client and operations  
- User profiles and agent management
- Settings management and persistence
- Database models and schema operations
"""

from .client import DatabaseClient, get_database_client, initialize_database
from .settings import get_system_defaults, load_user_agent_settings, save_user_agent_settings
from .models import UserProfile, AgentSettings, RunRecord

__all__ = [
    "DatabaseClient",
    "get_database_client", 
    "initialize_database",
    "get_system_defaults",
    "load_user_agent_settings",
    "save_user_agent_settings",
    "UserProfile",
    "AgentSettings", 
    "RunRecord",
]