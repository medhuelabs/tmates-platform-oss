"""User profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_database_with_user
from app.api.schemas import UserProfile, UserProfileUpdateRequest
from app.auth.manager import get_auth_manager

router = APIRouter()


@router.get("/profile", response_model=UserProfile, status_code=status.HTTP_200_OK)
def get_profile(context=Depends(get_database_with_user)) -> UserProfile:
    """Return the authenticated user's profile."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    auth_manager = get_auth_manager()
    auth_user = auth_manager.get_auth_user(user_id)
    profile = db.get_user_profile_by_auth_id(user_id)

    if not profile and not auth_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    email = auth_user.get("email") if auth_user else None
    display_name = auth_user.get("display_name") if auth_user else None

    if not display_name and email:
        display_name = email.split("@", 1)[0]

    if not profile and email:
        db.create_user_profile(user_id, email)
        profile = db.get_user_profile_by_auth_id(user_id)

    avatar_url = profile.get("avatar_url") if profile else None
    
    # Get user's organization role
    user_role = None
    organization = db.get_user_organization(user_id)
    if organization:
        membership = db.get_organization_membership(user_id, organization.get("id"))
        if membership:
            user_role = membership.get("role")

    return UserProfile(
        id=user_id,
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
        role=user_role,
    )


@router.patch("/profile", response_model=UserProfile, status_code=status.HTTP_200_OK)
def update_profile(
    payload: UserProfileUpdateRequest,
    context=Depends(get_database_with_user),
) -> UserProfile:
    """Update editable fields on the authenticated user's profile."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    auth_manager = get_auth_manager()
    auth_user = auth_manager.get_auth_user(user_id)
    profile = db.get_user_profile_by_auth_id(user_id)

    if not profile and auth_user:
        email = auth_user.get("email")
        fallback_display = auth_user.get("display_name") or (
            email.split("@", 1)[0] if email else user_id
        )
        db.create_user_profile(user_id, email or "")
        profile = db.get_user_profile_by_auth_id(user_id)

    if not profile and not auth_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    updates = {}

    if payload.display_name is not None:
        if not auth_manager.update_auth_user_display_name(user_id, payload.display_name):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to update authentication profile display name",
            )
        auth_user = auth_manager.get_auth_user(user_id)  # Refresh cache after update

    if payload.avatar_url is not None:
        updates["avatar_url"] = str(payload.avatar_url)

    current_email = auth_user.get("email") if auth_user else None

    if payload.email is not None:
        if payload.email != current_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Email updates require verification via the authentication flow."
                ),
            )

    if not updates:
        # Get user's organization role
        user_role = None
        organization = db.get_user_organization(user_id)
        if organization:
            membership = db.get_organization_membership(user_id, organization.get("id"))
            if membership:
                user_role = membership.get("role")
        
        return UserProfile(
            id=user_id,
            email=current_email,
            display_name=(
                auth_user.get("display_name")
                if auth_user and auth_user.get("display_name")
                else None
            ),
            avatar_url=profile.get("avatar_url") if profile else None,
            role=user_role,
        )

    if updates:
        updated = db.update_user_profile(user_id, updates)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update profile",
            )

    refreshed = db.get_user_profile_by_auth_id(user_id)

    final_display = auth_user.get("display_name") if auth_user and auth_user.get("display_name") else None

    if not final_display and current_email:
        final_display = current_email.split("@", 1)[0]

    # Get user's organization role
    user_role = None
    organization = db.get_user_organization(user_id)
    if organization:
        membership = db.get_organization_membership(user_id, organization.get("id"))
        if membership:
            user_role = membership.get("role")

    return UserProfile(
        id=user_id,
        email=current_email,
        display_name=final_display,
        avatar_url=refreshed.get("avatar_url") if refreshed else None,
        role=user_role,
    )
