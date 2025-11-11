from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.pinboard import PinboardPost, create_pinboard_post


def _base_record(**overrides: Any) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "id": "post-1",
        "title": "Sample",
        "slug": "sample",
        "content_md": "# Heading",
        "excerpt": "Heading",
        "author_agent_key": "demo",
        "cover_url": None,
        "attachments": [],
        "sources": [],
        "created_at": now,
        "updated_at": now,
        "organization_id": "org-1",
        "user_id": "user-1",
        "priority": overrides.pop("priority", None),
    }
    record.update(overrides)
    return record


def test_pinboard_post_from_record_defaults_priority() -> None:
    post = PinboardPost.from_record(_base_record())
    assert post.priority == "normal"


def test_pinboard_post_from_record_sanitizes_priority() -> None:
    post = PinboardPost.from_record(_base_record(priority="HIGH"))
    assert post.priority == "high"


class _DummyDatabase:
    def __init__(self) -> None:
        self.captured_priority: Optional[str] = None

    def get_pinboard_post_by_slug(self, *, organization_id: Optional[str], slug: str) -> Optional[Dict[str, Any]]:
        return None

    def create_pinboard_post(self, **kwargs: Any) -> Dict[str, Any]:
        self.captured_priority = kwargs.get("priority")
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": "created-1",
            "title": kwargs["title"],
            "slug": kwargs["slug"],
            "content_md": kwargs["content_md"],
            "excerpt": kwargs.get("excerpt"),
            "author_agent_key": kwargs["author_agent_key"],
            "cover_url": kwargs.get("cover_url"),
            "priority": kwargs.get("priority"),
            "attachments": kwargs.get("attachments", []),
            "sources": kwargs.get("sources", []),
            "created_at": now,
            "updated_at": now,
            "organization_id": kwargs.get("organization_id"),
            "user_id": kwargs.get("user_id"),
        }


def test_create_pinboard_post_sanitizes_priority() -> None:
    db = _DummyDatabase()
    post = create_pinboard_post(
        db,
        organization_id="org-1",
        user_id="user-1",
        title="Daily Update",
        content_md="Content",
        author_agent_key="demo",
        priority="URGENT",
    )

    assert db.captured_priority == "urgent"
    assert post.priority == "urgent"
