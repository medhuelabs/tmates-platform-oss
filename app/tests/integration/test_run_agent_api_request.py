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

    async def fake_run_prompt(message, user_id, session_id, context=None, attachments=None):
        recorded["run_prompt_args"] = (message, user_id, session_id, context, attachments)
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
    assert recorded["run_prompt_args"][4] is None
    assert recorded["context_inputs"][1] == user_context.user_id


def test_run_agent_api_request_with_vision(monkeypatch: pytest.MonkeyPatch, user_context: UserContext) -> None:
    recorded: dict[str, object] = {}

    class DummySessionManager:
        def get_or_create_session(self, **_):
            return "session-vision"

        def update_session_activity(self, session_id: str) -> None:  # pragma: no cover - unused
            recorded["updated_session"] = session_id

    async def fake_run_prompt(message, user_id, session_id, context=None, attachments=None):
        recorded["run_prompt_args"] = (message, user_id, session_id, context, attachments)
        return "img"

    def fake_prepare(inputs, ctx):
        recorded["vision_source"] = inputs
        recorded["vision_ctx"] = ctx.user_id
        return [{"type": "input_image", "image_url": "data:image/png;base64,AAA="}]

    monkeypatch.setattr(sdk_api, "session_manager", DummySessionManager())
    monkeypatch.setattr(sdk_api, "_prepare_vision_inputs", fake_prepare)

    payload = {
        "message": "Describe",
        "thread_id": "thread-vision",
        "attachments": [{"relative_path": "foo.png", "mime_type": "image/png"}],
    }

    response = sdk_api.run_agent_api_request(
        agent_key="adam",
        author_name="Adam",
        request=payload,
        user_context=user_context,
        run_prompt=fake_run_prompt,
        vision_enabled=True,
    )

    assert response["success"] is True
    assert recorded["vision_source"] == payload["attachments"]
    assert recorded["vision_ctx"] == user_context.user_id
    assert recorded["run_prompt_args"][4] == [{"type": "input_image", "image_url": "data:image/png;base64,AAA="}]


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
