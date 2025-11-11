"""Mobile settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_database_with_user
from app.api.schemas import MobileSettings, MobileSettingsUpdateRequest
from app.db.settings import load_user_mobile_settings, save_user_mobile_settings

router = APIRouter()


@router.get("/settings/mobile", response_model=MobileSettings, status_code=status.HTTP_200_OK)
def get_mobile_settings(context=Depends(get_database_with_user)) -> MobileSettings:
    """Return the authenticated user's mobile preference settings."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    settings = load_user_mobile_settings(user_id)
    return MobileSettings(**settings)


@router.patch("/settings/mobile", response_model=MobileSettings, status_code=status.HTTP_200_OK)
def update_mobile_settings(
    payload: MobileSettingsUpdateRequest,
    context=Depends(get_database_with_user),
) -> MobileSettings:
    """Persist mobile preference updates for the authenticated user."""

    user_id, db = context
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    updates = payload.model_dump(exclude_none=True)
    if updates.get("allow_notifications") is False:
        updates.setdefault("mentions", False)
        updates.setdefault("direct_messages", False)
        updates.setdefault("team_messages", False)

    success, merged_settings = save_user_mobile_settings(user_id, updates)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist mobile settings",
        )

    return MobileSettings(**merged_settings)
