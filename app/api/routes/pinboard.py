"""Pinboard endpoints for mobile and external clients."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.dependencies import get_current_user_id, get_database
from app.api.schemas import PinboardAttachment, PinboardPost, PinboardSource
from app.core.agent_runner import resolve_user_context
from app.registry.agents.store import AgentStore
from app.services.pinboard import PinboardPost as PinboardRecord
from app.services.pinboard import list_pinboard_posts, create_pinboard_post

router = APIRouter()
_agent_store = AgentStore()


class CreatePinboardPostRequest(BaseModel):
    title: str
    content_md: str
    excerpt: Optional[str] = None
    author_agent_key: Optional[str] = None
    cover_url: Optional[str] = None
    priority: Optional[int] = None
    attachments: Optional[List[dict]] = None
    sources: Optional[List[dict]] = None


def _resolve_agent_display(agent_key: Optional[str]) -> Optional[str]:
    if not agent_key:
        return None
    agent = _agent_store.get_agent(agent_key)
    if agent:
        return agent.name
    return agent_key.title()


def _convert_attachment(payload: dict) -> PinboardAttachment:
    return PinboardAttachment(
        url=str(payload.get("url") or ""),
        label=payload.get("label") or payload.get("name"),
        type=payload.get("type"),
    )


def _convert_source(payload: dict) -> PinboardSource:
    return PinboardSource(
        url=str(payload.get("url") or ""),
        label=payload.get("label") or payload.get("title"),
    )


def _convert_post(record: PinboardRecord) -> PinboardPost:
    attachments = [
        _convert_attachment(entry)
        for entry in record.attachments
        if isinstance(entry, dict)
    ]
    sources = [
        _convert_source(entry)
        for entry in record.sources
        if isinstance(entry, dict)
    ]
    return PinboardPost(
        id=record.id,
        title=record.title,
        slug=record.slug,
        excerpt=record.excerpt,
        content_md=record.content_md,
        author_agent_key=record.author_agent_key,
        author_display=_resolve_agent_display(record.author_agent_key),
        cover_url=record.cover_url,
        priority=record.priority,
        created_at=record.created_at,
        updated_at=record.updated_at,
        attachments=attachments,
        sources=sources,
    )


@router.get("/pinboard", response_model=List[PinboardPost], status_code=status.HTTP_200_OK)
def list_posts(
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_database),
    limit: int = Query(default=25, ge=1, le=100),
) -> List[PinboardPost]:
    """Return recent pinboard posts for the authenticated user."""

    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        _, organization, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve user context",
        ) from exc

    posts = list_pinboard_posts(
        db,
        organization_id=organization.get("id"),
        user_id=user_id,
        limit=limit,
    )
    return [_convert_post(post) for post in posts]


@router.get("/pinboard/{slug}", response_model=PinboardPost, status_code=status.HTTP_200_OK)
def get_post(
    slug: str,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_database),
) -> PinboardPost:
    """Return a single pinboard post by slug."""

    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        _, organization, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    record = db.get_pinboard_post_by_slug(
        organization_id=organization.get("id"),
        slug=slug,
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pinboard post not found",
        )

    pinboard_record = PinboardRecord.from_record(record)
    return _convert_post(pinboard_record)


@router.post("/pinboard", response_model=PinboardPost, status_code=status.HTTP_201_CREATED)
def create_post(
    request: CreatePinboardPostRequest,
    user_id: str = Depends(get_current_user_id),
    db=Depends(get_database),
) -> PinboardPost:
    """Create a new pinboard post."""

    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database client is not configured",
        )

    try:
        _, organization, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve user context",
        ) from exc

    # Create the pinboard post
    try:
        pinboard_record = create_pinboard_post(
            db=db,
            organization_id=organization.get("id"),
            user_id=user_id,
            title=request.title,
            content_md=request.content_md,
            excerpt=request.excerpt,
            author_agent_key=request.author_agent_key,
            cover_url=request.cover_url,
            priority=request.priority or 5,
            attachments=request.attachments or [],
            sources=request.sources or [],
        )
        return _convert_post(pinboard_record)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create pinboard post: {exc}",
        ) from exc
