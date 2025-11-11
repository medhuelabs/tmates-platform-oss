"""Pinboard service for storing agent-authored posts."""

from __future__ import annotations

import re
import textwrap
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.db.client import DatabaseClient


@dataclass
class PinboardPost:
    id: str
    title: str
    slug: str
    content_md: str
    excerpt: Optional[str]
    author_agent_key: str
    cover_url: Optional[str]
    priority: str
    attachments: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    organization_id: Optional[str]
    user_id: Optional[str]

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "PinboardPost":
        return cls(
            id=str(record.get("id")),
            title=str(record.get("title") or ""),
            slug=str(record.get("slug") or ""),
            content_md=str(record.get("content_md") or ""),
            excerpt=record.get("excerpt"),
            author_agent_key=str(record.get("author_agent_key") or ""),
            cover_url=record.get("cover_url"),
            priority=_normalize_priority(record.get("priority")),
            attachments=list(record.get("attachments") or []),
            sources=list(record.get("sources") or []),
            created_at=_parse_datetime(record.get("created_at")),
            updated_at=_parse_datetime(record.get("updated_at")),
            organization_id=record.get("organization_id"),
            user_id=record.get("user_id"),
        )


SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def list_pinboard_posts(
    db: DatabaseClient,
    *,
    organization_id: Optional[str],
    user_id: Optional[str],
    limit: int = 25,
) -> List[PinboardPost]:
    records = db.list_pinboard_posts(
        organization_id=organization_id,
        user_id=user_id,
        limit=limit,
    )
    return [PinboardPost.from_record(record) for record in records]


def create_pinboard_post(
    db: DatabaseClient,
    *,
    organization_id: Optional[str],
    user_id: Optional[str],
    title: str,
    content_md: str,
    author_agent_key: str,
    excerpt: Optional[str] = None,
    cover_url: Optional[str] = None,
    attachments: Optional[Sequence[Dict[str, Any]]] = None,
    sources: Optional[Sequence[Dict[str, Any]]] = None,
    slug: Optional[str] = None,
    priority: Optional[str] = None,
) -> PinboardPost:
    sanitized_attachments = _sanitize_dict_sequence(attachments or [])
    sanitized_sources = _sanitize_dict_sequence(sources or [])
    normalized_priority = _normalize_priority(priority)

    generated_slug = slug or _generate_slug(title)
    if db.get_pinboard_post_by_slug(organization_id=organization_id, slug=generated_slug):
        generated_slug = f"{generated_slug}-{uuid.uuid4().hex[:6]}"

    normalized_excerpt = excerpt or _generate_excerpt(content_md)

    record = db.create_pinboard_post(
        organization_id=organization_id,
        user_id=user_id,
        author_agent_key=author_agent_key,
        title=title.strip(),
        slug=generated_slug,
        content_md=content_md,
        excerpt=normalized_excerpt,
        cover_url=cover_url.strip() if cover_url else None,
        attachments=sanitized_attachments,
        sources=sanitized_sources,
        priority=normalized_priority,
    )
    if not record:
        raise RuntimeError("Failed to create pinboard post")
    return PinboardPost.from_record(record)


def delete_pinboard_post(
    db: DatabaseClient,
    *,
    post_id: str,
    organization_id: Optional[str],
    user_id: Optional[str],
) -> bool:
    return db.delete_pinboard_post(
        post_id=post_id,
        organization_id=organization_id,
        user_id=user_id,
    )


def _generate_slug(title: str) -> str:
    base = SLUG_PATTERN.sub("-", title.lower()).strip("-")
    if not base:
        base = "post"
    return base[:64]


def _generate_excerpt(content: str, *, max_length: int = 280) -> str:
    stripped = content.strip()
    if not stripped:
        return ""
    condensed = " ".join(stripped.split())
    return textwrap.shorten(condensed, width=max_length, placeholder="…")


def _sanitize_dict_sequence(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sanitized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = {
            key: value
            for key, value in item.items()
            if isinstance(key, str) and value is not None
        }
        if normalized:
            sanitized.append(normalized)
    return sanitized


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


_PINBOARD_PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")
_DEFAULT_PRIORITY = "normal"


def _normalize_priority(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _PINBOARD_PRIORITIES:
            return candidate
    return _DEFAULT_PRIORITY


__all__ = [
    "PinboardPost",
    "list_pinboard_posts",
    "create_pinboard_post",
    "delete_pinboard_post",
]
