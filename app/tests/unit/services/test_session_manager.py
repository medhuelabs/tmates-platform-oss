"""Unit tests for the stateless session manager."""

from __future__ import annotations

import uuid

from app.services.session_manager import SessionManager
from app.auth import UserContext


def _user_context() -> UserContext:
    return UserContext(
        user_id="user-42",
        display_name="Tester",
        email="tester@example.com",
        enabled_agents=["adam", "nolan"],
        agent_configs={},
        timezone="UTC",
    )


def test_get_or_create_session_prefers_provided_id() -> None:
    manager = SessionManager()
    ctx = _user_context()

    session_id = manager.get_or_create_session(
        user_context=ctx,
        thread_id="thread-1",
        agent_key="adam",
        provided_session_id="existing-session",
    )

    assert session_id == "existing-session"


def test_get_or_create_session_generates_stable_id() -> None:
    manager = SessionManager()
    ctx = _user_context()

    session_a = manager.get_or_create_session(ctx, "thread-2", "adam")
    session_b = manager.get_or_create_session(ctx, "thread-2", "adam")
    session_other_thread = manager.get_or_create_session(ctx, "thread-3", "adam")

    assert session_a == session_b
    assert session_a != session_other_thread


def test_session_helpers_return_truthy() -> None:
    manager = SessionManager()
    fake_session = uuid.uuid4().hex

    assert manager.update_session_activity(fake_session) is True
    assert manager.end_session(fake_session) is True
    assert manager.cleanup_expired_sessions() == 0
    assert manager.get_session_info(fake_session) is None
    assert manager.get_active_sessions_for_user("user-42") == {}
