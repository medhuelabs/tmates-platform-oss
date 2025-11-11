"""
Authentication and User Context Management

This module provides:
- User authentication via Supabase Auth
- JWT token validation and session management  
- User context management for multi-user operations
- User profile and credential handling
"""

from .manager import AuthManager, get_auth_manager, require_auth
from .user_context import UserContext, get_default_user_context, load_user_context_from_env, encrypt_token, decrypt_token

__all__ = [
    'AuthManager',
    'get_auth_manager', 
    'require_auth',
    'UserContext',
    'get_default_user_context',
    'load_user_context_from_env',
    'encrypt_token',
    'decrypt_token'
]