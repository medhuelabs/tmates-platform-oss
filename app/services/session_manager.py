"""
Session Management Service for Agent Conversations.

The original implementation stored session state in-process, which breaks during
horizontal scaling or worker restarts. This revision keeps the public API but
generates deterministic session identifiers so that clients can continue their
conversation threads without relying on mutable server-side state.
"""

from __future__ import annotations

import uuid
from typing import Dict, Optional

from app.auth import UserContext


class SessionInfo:
    """Lightweight container returned for API compatibility."""

    __slots__ = ("session_id", "user_id", "agent_key", "thread_id")

    def __init__(self, session_id: str, user_id: str, agent_key: str, thread_id: str) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.agent_key = agent_key
        self.thread_id = thread_id


class SessionManager:
    """Stateless session helper for agent conversations."""

    # Preserved for backwards compatibility with callers that inspect the constant.
    SESSION_TIMEOUT_MINUTES = 30

    @staticmethod
    def _stable_session_id(user_id: str, thread_id: str, agent_key: str) -> str:
        namespaced = f"{user_id}:{thread_id}:{agent_key}"
        return uuid.uuid5(uuid.NAMESPACE_URL, namespaced).hex

    def get_or_create_session(
        self,
        user_context: UserContext,
        thread_id: str,
        agent_key: str,
        provided_session_id: Optional[str] = None,
    ) -> str:
        """
        Return a session identifier for the conversation.

        The identifier is deterministic for the (user, thread, agent) tuple so that
        subsequent requests continue the same context even after process restarts.
        """
        if provided_session_id:
            return provided_session_id

        user_id = user_context.user_id
        return self._stable_session_id(user_id, thread_id, agent_key)

    def update_session_activity(self, session_id: str) -> bool:  # pragma: no cover - trivial
        """Retained for compatibility; stateless implementation always succeeds."""
        return bool(session_id)

    def end_session(self, session_id: str) -> bool:  # pragma: no cover - trivial
        """Stateless implementation treats end_session as a no-op."""
        return bool(session_id)

    def cleanup_expired_sessions(self) -> int:  # pragma: no cover - trivial
        """Nothing to clean up in stateless mode."""
        return 0

    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:  # pragma: no cover - trivial
        """Return synthetic session info for compatibility."""
        if not session_id:
            return None
        # Without persisted metadata we return None, signaling no extra data.
        return None

    def get_active_sessions_for_user(self, user_id: str) -> Dict[str, SessionInfo]:  # pragma: no cover - trivial
        """Stateless implementation has no per-user cache."""
        return {}


session_manager = SessionManager()
