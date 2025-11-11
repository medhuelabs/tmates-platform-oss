"""Tool definitions for the Dana agent."""

from __future__ import annotations

import os
import re
from typing import Annotated, Any, List, Optional, Sequence

from agents import RunContextWrapper, function_tool

from app.services.google.gmail import EmailMessage, GmailAuthError, GmailCredentialsError, GmailService


SERVICE = GmailService()
USER_ID_KEYS: tuple[str, ...] = ("user_id", "auth_user_id", "supabase_user_id", "id", "uid")


def _coerce_recipients(value: Optional[Sequence[str] | str]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[;,]", value)
        return [part.strip() for part in parts if part and part.strip()]
    recipients: List[str] = []
    for entry in value:
        if entry:
            text = str(entry).strip()
            if text:
                recipients.append(text)
    return recipients


def _extract_from_mapping(mapping: Any) -> Optional[str]:
    if not isinstance(mapping, dict):
        return None
    for key in USER_ID_KEYS:
        candidate = mapping.get(key)
        if candidate:
            return str(candidate)
    return None


def _extract_user_id(ctx: RunContextWrapper[Any]) -> str:
    """Try to resolve the auth user id from the run context."""

    for attr in ("user_id", "auth_user_id", "supabase_user_id"):
        value = getattr(ctx, attr, None)
        if value:
            return str(value)

    for attr in ("context", "metadata", "state"):
        mapping = getattr(ctx, attr, None)
        candidate = _extract_from_mapping(mapping)
        if candidate:
            return candidate

    env_user = os.getenv("USER_ID") or os.getenv("AUTH_USER_ID")
    if env_user:
        return env_user

    raise RuntimeError("Unable to determine user identity for Gmail operations.")


def _handle_service_error(exc: Exception) -> RuntimeError:
    if isinstance(exc, (GmailAuthError, GmailCredentialsError)):
        return RuntimeError(str(exc))
    return RuntimeError(f"Gmail operation failed: {exc}")


@function_tool
async def request_gmail_login_link(ctx: RunContextWrapper[Any]) -> dict[str, str]:
    """
    Create a one-time Google OAuth authorization URL for the current user.

    Returns the authorization URL and state token that the frontend must retain.
    """

    user_id = _extract_user_id(ctx)
    try:
        url, state = SERVICE.generate_authorization_url(user_id)
        return {"authorization_url": url, "state": state.encode()}
    except Exception as exc:  # pragma: no cover - service handles detailed error types
        raise _handle_service_error(exc)


@function_tool
async def gmail_connection_status(ctx: RunContextWrapper[Any]) -> dict[str, Any]:
    """Retrieve the stored Gmail connection status for the authenticated user."""

    user_id = _extract_user_id(ctx)
    try:
        return SERVICE.get_connection_status(user_id)
    except Exception as exc:  # pragma: no cover - service handles detailed error types
        raise _handle_service_error(exc)


@function_tool
async def gmail_search_messages(
    ctx: RunContextWrapper[Any],
    query: Annotated[Optional[str], "Gmail search query, e.g., 'from:team@tmates.app is:unread'."] = None,
    max_results: Annotated[int, "Maximum number of messages to return (1-20)."] = 10,
    label_ids: Annotated[Optional[Sequence[str]], "Optional list of Gmail label IDs to filter by."] = None,
) -> List[dict[str, Any]]:
    """Search the user's mailbox and return lightweight message summaries."""

    user_id = _extract_user_id(ctx)
    if max_results < 1:
        max_results = 1
    if max_results > 20:
        max_results = 20

    try:
        summaries = SERVICE.list_messages(
            user_id,
            query=query,
            label_ids=label_ids,
            max_results=max_results,
        )
        return [summary.dict() for summary in summaries]
    except Exception as exc:  # pragma: no cover - service handles detailed error types
        raise _handle_service_error(exc)


@function_tool
async def gmail_read_message(
    ctx: RunContextWrapper[Any],
    message_id: Annotated[str, "Unique Gmail message identifier."],
) -> dict[str, Any]:
    """Fetch the full content for a specific Gmail message."""

    user_id = _extract_user_id(ctx)
    try:
        message: EmailMessage = SERVICE.get_message(user_id, message_id)
        return message.dict()
    except Exception as exc:  # pragma: no cover - service handles detailed error types
        raise _handle_service_error(exc)


@function_tool
async def gmail_send_email(
    ctx: RunContextWrapper[Any],
    to: Annotated[Sequence[str] | str, "Comma-separated or list of primary recipients."],
    subject: Annotated[str, "Subject line for the email."],
    body: Annotated[str, "Email body. Use plain text by default."],
    cc: Annotated[Optional[Sequence[str] | str], "Optional CC recipients."] = None,
    bcc: Annotated[Optional[Sequence[str] | str], "Optional BCC recipients."] = None,
    reply_to: Annotated[Optional[str], "Optional reply-to header value."] = None,
    html: Annotated[bool, "Set to true to send the body as HTML."] = False,
    thread_id: Annotated[Optional[str], "Optional thread id to reply within an existing conversation."] = None,
) -> dict[str, Any]:
    """Send an email on behalf of the connected Gmail account."""

    user_id = _extract_user_id(ctx)
    to_recipients = _coerce_recipients(to)
    if not to_recipients:
        raise RuntimeError("At least one 'to' recipient is required.")

    try:
        response = SERVICE.send_message(
            user_id,
            to=to_recipients,
            subject=subject,
            body=body,
            cc=_coerce_recipients(cc),
            bcc=_coerce_recipients(bcc),
            reply_to=reply_to,
            html=html,
            thread_id=thread_id,
        )
        return {
            "id": response.get("id"),
            "thread_id": response.get("threadId"),
            "label_ids": response.get("labelIds"),
        }
    except Exception as exc:  # pragma: no cover - service handles detailed error types
        raise _handle_service_error(exc)
