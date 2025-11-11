"""
Authentication module for Supabase integration.

This module provides:
- JWT token validation
- User authentication helpers
- Session management
- User profile integration
"""

import os
from typing import Any, Dict, Optional
import logging

import jwt
import base64
import binascii
import requests
from fastapi import HTTPException, status
from supabase import Client, create_client

from ..config import CONFIG


logger = logging.getLogger(__name__)

class SupabaseAuthManager:
    """Manages authentication with Supabase Auth."""
    
    def __init__(self):
        """Initialize the AuthManager with Supabase client."""
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
        self.supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        self.jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
        if not all([self.supabase_url, self.supabase_anon_key]):
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY environment variables are required")
        
        client_key = self.supabase_service_role_key or self.supabase_anon_key
        if not self.supabase_service_role_key:
            logger.warning("SUPABASE_SERVICE_ROLE_KEY not set; falling back to anon key for auth verification")
        self.supabase: Client = create_client(self.supabase_url, client_key)
        self._jwt_secret_candidates = self._prepare_jwt_secret_candidates(self.jwt_secret)

    @staticmethod
    def _prepare_jwt_secret_candidates(secret: Optional[str]) -> list:
        candidates: list = []
        if not secret:
            return candidates

        raw = secret.strip()
        if raw:
            candidates.append(raw)
            try:
                decoded = base64.b64decode(raw, validate=True)
                if decoded:
                    candidates.append(decoded)
            except (binascii.Error, ValueError):
                pass
        return candidates

    def _load_user_via_supabase(self, token: str) -> Optional[Dict[str, Any]]:
        """Fallback to Supabase SDK for token validation."""
        try:
            user = self.supabase.auth.get_user(token)
        except Exception as exc:
            logger.warning("Supabase auth get_user raised an exception: %s", exc)
            return None
        if not user or not getattr(user, "user", None):
            return None
        supa_user = user.user
        return {
            "sub": supa_user.id,
            "email": supa_user.email,
            "user_metadata": supa_user.user_metadata or {},
        }
    
    def verify_jwt_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify and decode a JWT token from Supabase Auth.

        Args:
            token: The JWT token to verify

        Returns:
            Decoded token payload if valid, None if invalid
        """
        if not token:
            logger.debug("verify_jwt_token received empty token")
            return None
        logger.debug("verify_jwt_token length=%s", len(token))
        try:
            if self._jwt_secret_candidates:
                for candidate in self._jwt_secret_candidates:
                    try:
                        payload = jwt.decode(
                            token,
                            candidate,
                            algorithms=["HS256"],
                            audience="authenticated",
                        )
                        logger.debug("JWT decoded using configured secret candidate")
                        return payload
                    except jwt.InvalidTokenError:
                        logger.debug("JWT decode failed for one candidate; trying next")
                        continue
            logger.debug("Falling back to Supabase SDK token validation")
            result = self._load_user_via_supabase(token)
            if not result:
                logger.warning("Supabase SDK could not validate token")
            return result
        except Exception:
            logger.exception("Unexpected error while decoding JWT; falling back to Supabase SDK")
            return self._load_user_via_supabase(token)
    
    def get_user_from_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Extract user information from a valid JWT token.
        
        Args:
            token: The JWT token
            
        Returns:
            User information dict or None if invalid
        """
        payload = self.verify_jwt_token(token)
        if not payload:
            return None
        
        return {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "metadata": payload.get("user_metadata", {})
        }
    
    def create_user_profile(self, user_id: str, email: str, display_name: str = None) -> bool:
        """Create or update a Supabase-backed user profile."""
        try:
            from app.db import get_database_client

            db = get_database_client()

            existing = db.get_user_profile_by_auth_id(user_id)
            if existing:
                return True

            created = db.create_user_profile(
                user_id,
                email,
            )
            return created is not None

        except Exception as exc:
            print(f"Error creating user profile: {exc}")
            return False

    def _admin_request(self, method: str, path: str, **kwargs: Any) -> Optional[requests.Response]:
        """Perform an auth admin request when a service role key is available."""

        if not self.supabase_service_role_key or not self.supabase_url:
            logger.debug("Supabase admin request skipped; missing service role key or URL")
            return None

        url = f"{self.supabase_url.rstrip('/')}/auth/v1{path}"
        headers = kwargs.pop("headers", {})
        headers.setdefault("Authorization", f"Bearer {self.supabase_service_role_key}")
        headers.setdefault("apikey", self.supabase_service_role_key)
        if "json" in kwargs and kwargs["json"] is not None:
            headers.setdefault("Content-Type", "application/json")

        try:
            response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
        except Exception:
            logger.exception("Supabase admin request failed", extra={"method": method, "path": path})
            return None

        if response.status_code >= 400:
            logger.warning(
                "Supabase admin request %s %s returned %s: %s",
                method,
                path,
                response.status_code,
                response.text,
            )
        return response
    
    def authenticate_request_token(self, authorization_header: str) -> Optional[str]:
        """
        Extract and validate JWT token from Authorization header.
        
        Args:
            authorization_header: The Authorization header value
            
        Returns:
            User ID if valid, None if invalid
        """
        if not authorization_header or not authorization_header.startswith("Bearer "):
            return None
        
        token = authorization_header[7:]  # Remove "Bearer " prefix
        user_info = self.get_user_from_token(token)
        return user_info.get("id") if user_info else None

    def get_auth_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the authoritative auth user record from Supabase."""

        if not user_id:
            return None

        response = self._admin_request("GET", f"/admin/users/{user_id}")
        if not response or response.status_code != 200:
            return None

        data = response.json()
        metadata = data.get("user_metadata") or {}
        raw_metadata = data.get("raw_user_meta_data") or {}
        display_name = (
            metadata.get("full_name")
            or metadata.get("display_name")
            or metadata.get("name")
            or raw_metadata.get("full_name")
            or raw_metadata.get("display_name")
            or raw_metadata.get("name")
            or data.get("display_name")
        )
        email = data.get("email")
        if not display_name and email:
            display_name = email.split("@")[0]

        return {
            "id": data.get("id"),
            "email": email,
            "display_name": display_name,
            "user_metadata": metadata,
            "raw": data,
        }

    def update_auth_user_display_name(
        self, user_id: str, display_name: Optional[str]
    ) -> bool:
        """Update the Supabase auth user's display name metadata."""

        if not user_id:
            return False

        if not self.supabase_service_role_key or not self.supabase_url:
            logger.debug(
                "Skipping auth display name update; service role key not configured"
            )
            return True

        existing = self.get_auth_user(user_id) or {}
        metadata = dict(existing.get("user_metadata") or {})

        if display_name and display_name.strip():
            metadata["full_name"] = display_name.strip()
        else:
            metadata.pop("full_name", None)

        response = self._admin_request(
            "PUT",
            f"/admin/users/{user_id}",
            json={"user_metadata": metadata},
        )

        return bool(response and response.status_code < 400)


AuthManager = SupabaseAuthManager


# Global auth manager instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get the global AuthManager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager


def require_auth(authorization: str = None) -> str:
    """
    FastAPI dependency to require authentication.
    
    Args:
        authorization: Authorization header value
        
    Returns:
        User ID if authenticated
        
    Raises:
        HTTPException: If authentication fails
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    auth_manager = get_auth_manager()
    user_id = auth_manager.authenticate_request_token(authorization)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return user_id
