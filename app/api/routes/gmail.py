"""FastAPI routes for managing Gmail OAuth connections."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user_id
from app.services.google.gmail import (
    GmailAuthError,
    GmailCredentialsError,
    GmailProfile,
    GmailService,
)


router = APIRouter(prefix="/integrations/gmail")
gmail_service = GmailService()


class AuthorizationUrlResponse(BaseModel):
    authorization_url: str = Field(..., description="Google OAuth URL to redirect the user to.")
    state: str = Field(..., description="Opaque state parameter that must be returned in the callback.")


class OAuthExchangeRequest(BaseModel):
    code: str = Field(..., description="Authorization code returned by Google.")
    state: str = Field(..., description="Opaque state value included with the initial authorization URL.")


class OAuthExchangeResponse(BaseModel):
    connected: bool = Field(default=True, description="Indicates whether the Gmail account is now connected.")
    profile: GmailProfile = Field(..., description="Profile metadata for the authenticated Gmail account.")


class ConnectionStatusResponse(BaseModel):
    connected: bool
    email: Optional[str] = None
    updated_at: Optional[str] = None
    scopes: Optional[list[str]] = None


@router.get("/authorization-url", response_model=AuthorizationUrlResponse)
def get_authorization_url(user_id: str = Depends(get_current_user_id)) -> AuthorizationUrlResponse:
    """Generate a Google OAuth authorization URL for the authenticated user."""

    try:
        url, state = gmail_service.generate_authorization_url(user_id)
        return AuthorizationUrlResponse(authorization_url=url, state=state.encode())
    except GmailAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/exchange", response_model=OAuthExchangeResponse)
def exchange_authorization_code(
    payload: OAuthExchangeRequest,
    user_id: str = Depends(get_current_user_id),
) -> OAuthExchangeResponse:
    """Exchange the Google OAuth authorization code for tokens."""

    try:
        profile = gmail_service.exchange_authorization_code(
            payload.state,
            payload.code,
            expected_user_id=user_id,
        )
        return OAuthExchangeResponse(profile=profile)
    except GmailAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/callback")
def gmail_oauth_callback(request: Request) -> HTMLResponse:
    """
    Handle Google's OAuth redirect.

    This endpoint intentionally does not require authentication because Google
    will redirect the user-agent directly. The encoded OAuth state ties the
    callback to a specific user and pending session.
    """

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        content = (
            "<h1>Gmail connection failed</h1>"
            f"<p>Google returned an error: <strong>{error}</strong>.</p>"
            "<p>Please close this window and try again from tmates.</p>"
        )
        return HTMLResponse(content=content, status_code=status.HTTP_400_BAD_REQUEST)

    if not code or not state:
        return HTMLResponse(
            content="<h1>Missing OAuth parameters</h1><p>Google did not supply the required code/state.</p>",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        profile = gmail_service.exchange_authorization_code(state, code)
    except GmailAuthError as exc:
        return HTMLResponse(
            content=f"<h1>Gmail connection failed</h1><p>{exc}</p>",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    content = (
        "<h1>Gmail connected</h1>"
        f"<p>The account <strong>{profile.email_address}</strong> is now ready to use.</p>"
        "<p>You can close this window and return to tmates.</p>"
    )
    return HTMLResponse(content=content, status_code=status.HTTP_200_OK)


@router.get("/status", response_model=ConnectionStatusResponse)
def gmail_connection_status(user_id: str = Depends(get_current_user_id)) -> ConnectionStatusResponse:
    """Return the Gmail connection status for the authenticated user."""

    status_payload = gmail_service.get_connection_status(user_id)
    return ConnectionStatusResponse(**status_payload)


@router.post("/disconnect")
def gmail_disconnect(user_id: str = Depends(get_current_user_id)) -> JSONResponse:
    """Revoke and delete stored Gmail credentials for the user."""

    try:
        gmail_service.revoke_credentials(user_id)
    except GmailCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return JSONResponse({"success": True})
