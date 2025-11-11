"""Pinboard utilities that can be shared across agents."""

from __future__ import annotations

import os
from typing import Annotated, Any, Dict, Optional, Sequence

from agents import FunctionTool, RunContextWrapper, function_tool
from pydantic import BaseModel, Field

from app.db import DatabaseClient, get_database_client
from app.services.pinboard import PinboardPost, create_pinboard_post

try:  # pragma: no cover - fallback for environments without websocket wiring
    from app.api.routes.websocket import notify_pinboard_post
except Exception:  # noqa: BLE001
    async def notify_pinboard_post(user_id: str, post: dict, *, organization_id: str | None = None) -> None:
        return None


USER_ID_KEYS: tuple[str, ...] = ("user_id", "auth_user_id", "supabase_user_id", "id", "uid")


class PinboardAttachmentInput(BaseModel):
    """Structured metadata describing an attachment associated with a Pinboard post."""

    url: str = Field(description="Absolute URL for downloading or previewing the attachment.")
    label: str | None = Field(
        default=None,
        description="Optional short label that appears alongside the attachment.",
    )
    type: str | None = Field(
        default=None,
        description="Optional attachment category or MIME hint.",
    )


class PinboardSourceInput(BaseModel):
    """Metadata describing a source that inspired the Pinboard entry."""

    url: str = Field(description="Reference URL for the source material.")
    label: str | None = Field(
        default=None,
        description="Optional display name for the source.",
    )


def _extract_from_mapping(mapping: Any) -> Optional[str]:
    if not isinstance(mapping, dict):
        return None
    for key in USER_ID_KEYS:
        candidate = mapping.get(key)
        if candidate:
            return str(candidate)
    return None


def _extract_user_id(ctx: RunContextWrapper[Any]) -> str:
    """Best-effort extraction of the authenticated user identifier from the context."""

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

    raise RuntimeError("Unable to determine user identity for Pinboard operations.")


def _resolve_organization_id(db: DatabaseClient, user_id: str) -> Optional[str]:
    try:
        organization = db.get_user_organization(user_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        raise RuntimeError(f"Failed to resolve organization for user '{user_id}': {exc}") from exc

    if isinstance(organization, dict):
        org_id = organization.get("id")
        if org_id:
            return str(org_id)
    return None


def _serialize_pinboard_post(post: PinboardPost) -> Dict[str, Any]:
    return {
        "id": post.id,
        "slug": post.slug,
        "title": post.title,
        "excerpt": post.excerpt,
        "content_md": post.content_md,
        "author_agent_key": post.author_agent_key,
        "cover_url": post.cover_url,
        "priority": post.priority,
        "attachments": list(post.attachments),
        "sources": list(post.sources),
        "created_at": post.created_at.isoformat(),
        "updated_at": post.updated_at.isoformat(),
        "organization_id": post.organization_id,
        "user_id": post.user_id,
    }


def _dump_models(items: Optional[Sequence[BaseModel]]) -> Sequence[Dict[str, Any]]:
    if not items:
        return ()
    return [item.model_dump(exclude_none=True) for item in items]


def build_create_pinboard_post_tool(*, agent_key: str) -> FunctionTool:
    """
    Create a tool that publishes a Pinboard post on behalf of ``agent_key``.

    Each agent should call this helper once at module import time and include the resulting
    tool object in its ``agent.tools`` collection.
    """

    if not agent_key:
        raise ValueError("agent_key is required to build a Pinboard tool.")

    @function_tool
    async def create_pinboard_post_tool(
        ctx: RunContextWrapper[Any],
        title: Annotated[str, "Title for the post shown in Pinboard lists."],
        content_md: Annotated[str, "Markdown content saved with the Pinboard entry."],
        excerpt: Annotated[
            Optional[str],
            "Optional plain-text teaser used in list views. Auto-generated when omitted.",
        ] = None,
        cover_url: Annotated[
            Optional[str],
            "Optional cover image URL displayed with the post.",
        ] = None,
        attachments: Annotated[
            Optional[Sequence[PinboardAttachmentInput]],
            "Optional attachment metadata entries (URL required, label/type optional).",
        ] = None,
        sources: Annotated[
            Optional[Sequence[PinboardSourceInput]],
            "Optional source attribution metadata for the post.",
        ] = None,
        slug: Annotated[
            Optional[str],
            "Optional slug override. Defaults to a generated value when not provided.",
        ] = None,
        priority: Annotated[
            Optional[str],
            "Optional priority indicator ('low', 'normal', 'high', 'urgent'). Defaults to 'normal'.",
        ] = None,
    ) -> Dict[str, Any]:
        user_id = _extract_user_id(ctx)

        db = get_database_client()
        if db is None:
            raise RuntimeError("Database client is not configured for Pinboard operations.")

        organization_id = _resolve_organization_id(db, user_id)

        post = create_pinboard_post(
            db,
            organization_id=organization_id,
            user_id=user_id,
            title=title,
            content_md=content_md,
            author_agent_key=agent_key,
            excerpt=excerpt,
            cover_url=cover_url,
            attachments=_dump_models(attachments),
            sources=_dump_models(sources),
            slug=slug,
            priority=priority,
        )
        payload = _serialize_pinboard_post(post)

        try:
            await notify_pinboard_post(user_id, payload, organization_id=organization_id)
        except Exception as exc:  # pragma: no cover - notification failures shouldn't abort tool
            print(f"[pinboard-tool] Failed to push websocket notification: {exc}")

        return payload

    return create_pinboard_post_tool


__all__ = ["build_create_pinboard_post_tool"]
