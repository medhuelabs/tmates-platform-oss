"""Integration-style tests for the SDK API adapter."""

from __future__ import annotations

import pytest

from app.sdk.agents.tmates_agents_sdk import api as sdk_api
from app.auth import UserContext


def test_run_agent_api_request_requires_user_context(user_context: UserContext) -> None:
    response = sdk_api.run_agent_api_request(
        agent_key="adam",
        author_name="Adam",
        request={"message": "Hi", "thread_id": "t-1"},
        user_context=None,
        run_prompt=lambda *args, **kwargs: None,  # pragma: no cover - never called
    )

    assert response == {
        "success": False,
        "error": "User context required for session management",
        "error_type": "AuthenticationError",
        "thread_id": "t-1",
        "author": "Adam",
    }


def test_run_agent_api_request_success(monkeypatch: pytest.MonkeyPatch, user_context: UserContext) -> None:
    recorded: dict[str, object] = {}

    class DummySessionManager:
        def get_or_create_session(self, *, user_context, thread_id, agent_key, provided_session_id=None):
            recorded["session_request"] = (user_context.user_id, thread_id, agent_key, provided_session_id)
            return provided_session_id or "session-xyz"

        def update_session_activity(self, session_id: str) -> None:
            recorded["updated_session"] = session_id

    async def fake_run_prompt(message, user_id, session_id, context=None):
        recorded["run_prompt_args"] = (message, user_id, session_id, context)
        return "Thanks!"

    def build_context(request, user_id, session_id):
        recorded["context_inputs"] = (request, user_id, session_id)
        return {"user_id": user_id, "session_id": session_id}

    monkeypatch.setattr(sdk_api, "session_manager", DummySessionManager())
    monkeypatch.setattr(sdk_api, "consume_generated_attachments", lambda job_id: [{"name": "doc.pdf"}])

    request_payload = {
        "message": "Summarize document",
        "thread_id": "thread-9",
        "metadata": {"job_id": "job-7"},
        "session_id": "provided-session",
    }

    response = sdk_api.run_agent_api_request(
        agent_key="adam",
        author_name="Adam",
        request=request_payload,
        user_context=user_context,
        run_prompt=fake_run_prompt,
        include_generated_attachments=True,
        context_builder=build_context,
    )

    assert response["success"] is True
    assert response["response"] == "Thanks!"
    assert response["session_id"] == "provided-session"
    assert response["metadata"]["session_created"] is False
    assert response["attachments"] == [{"name": "doc.pdf"}]

    assert recorded["run_prompt_args"][0] == "Summarize document"
    assert recorded["run_prompt_args"][1] == user_context.user_id
    assert recorded["run_prompt_args"][2] == "provided-session"
    assert recorded["run_prompt_args"][3] == {"user_id": user_context.user_id, "session_id": "provided-session"}
    assert recorded["context_inputs"][1] == user_context.user_id


def test_run_agent_api_request_handles_run_prompt_error(monkeypatch: pytest.MonkeyPatch, user_context: UserContext) -> None:
    class DummySessionManager:
        def get_or_create_session(self, **kwargs):
            return "session-abc"

        def update_session_activity(self, session_id: str) -> None:
            return None

    async def failing_run_prompt(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sdk_api, "session_manager", DummySessionManager())

    response = sdk_api.run_agent_api_request(
        agent_key="adam",
        author_name="Adam",
        request={"message": "Hi", "thread_id": "t-99"},
        user_context=user_context,
        run_prompt=failing_run_prompt,
    )

    assert response["success"] is False
    assert response["error"] == "boom"
    assert response["error_type"] == "RuntimeError"
