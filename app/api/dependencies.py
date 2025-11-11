"""FastAPI dependencies shared across the public API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, Header, HTTPException, status

from ..auth import get_auth_manager, require_auth
from ..db import DatabaseClient, get_database_client


def get_current_user_id(authorization: str = Header(None)) -> str:
    """Resolve the authenticated Supabase user from the Authorization header."""

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return require_auth(authorization)


def get_database() -> DatabaseClient:
    """Return the shared database client instance."""

    return get_database_client()


def get_database_with_user(
    user_id: str = Depends(get_current_user_id),
) -> tuple[str, DatabaseClient]:
    """Convenience helper that returns both user id and database client."""

    return user_id, get_database_client()


def get_authenticated_user(authorization: str = Header(None)) -> Dict[str, Any]:
    """Return authenticated Supabase user details (ID, email, metadata)."""

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_manager = get_auth_manager()
    user_info = auth_manager.get_user_from_token(token)
    if not user_info or not user_info.get("id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    metadata = user_info.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": user_info.get("id"),
        "email": user_info.get("email"),
        "metadata": metadata,
    }
