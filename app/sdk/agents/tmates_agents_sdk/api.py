"""API adapter helpers for session-aware agent executions."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from app.auth import UserContext
from app.services.generated_media_registry import consume_generated_attachments
from app.services.session_manager import session_manager

from app.sdk.agents.tmates_agents_sdk.types import ContextBuilder, RunPromptCallable


def _ensure_event_loop() -> tuple[asyncio.AbstractEventLoop, bool, Optional[asyncio.AbstractEventLoop]]:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            return new_loop, True, loop
        return loop, False, None
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        return new_loop, True, None


def run_agent_api_request(
    *,
    agent_key: str,
    author_name: str,
    request: Dict[str, Any],
    user_context: Optional[UserContext],
    run_prompt: RunPromptCallable,
    include_generated_attachments: bool = False,
    context_builder: Optional[ContextBuilder] = None,
) -> Dict[str, Any]:
    """Common API handler wired for tmates agents."""

    message = request.get("message", "")
    thread_id = request.get("thread_id")
    author = request.get("author", "User")
    provided_session_id = request.get("session_id")

    if not user_context or not getattr(user_context, "user_id", None):
        return {
            "success": False,
            "error": "User context required for session management",
            "error_type": "AuthenticationError",
            "thread_id": thread_id,
            "author": author_name,
        }

    try:
        session_id = session_manager.get_or_create_session(
            user_context=user_context,
            thread_id=thread_id or "default",
            agent_key=agent_key,
            provided_session_id=provided_session_id,
        )

        user_id = user_context.user_id

        def _build_context() -> Optional[Dict[str, Any]]:
            if context_builder is None:
                return None
            return context_builder(request, user_id, session_id)

        async def _run_async() -> str:
            return await run_prompt(
                message,
                user_id,
                session_id,
                context=_build_context(),
            )

        loop, should_close, previous_loop = _ensure_event_loop()

        try:
            response_text = loop.run_until_complete(_run_async())
        except Exception as exc:  # pragma: no cover - defensive logging
            return {
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "thread_id": thread_id,
                "author": author_name,
                "session_id": provided_session_id,
            }
        finally:
            if should_close:
                if previous_loop is not None:
                    asyncio.set_event_loop(previous_loop)
                    loop.close()

        session_manager.update_session_activity(session_id)

        attachments = []
        if include_generated_attachments:
            metadata = request.get("metadata") or {}
            attachments = consume_generated_attachments(metadata.get("job_id"))

        response: Dict[str, Any] = {
            "success": True,
            "response": response_text,
            "thread_id": thread_id,
            "author": author_name,
            "session_id": session_id,
            "metadata": {
                "agent_key": agent_key,
                "processing_method": "api",
                "session_created": provided_session_id != session_id,
            },
        }

        if attachments:
            response["attachments"] = attachments

        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        return {
            "success": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "thread_id": thread_id,
            "author": author_name,
            "session_id": provided_session_id,
        }
