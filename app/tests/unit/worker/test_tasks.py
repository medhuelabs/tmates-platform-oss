"""Unit tests for worker task helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.worker import tasks


def test_strip_attachment_links_removes_urls() -> None:
    text = "Here is your file: https://example.com/download/123"
    attachments = [{"download_url": "https://example.com/download/123"}]

    result = tasks._strip_attachment_links(text, attachments)

    assert "https://" not in result
    assert "file" in result


def test_matches_transient_db_markers() -> None:
    assert tasks._matches_transient_db_markers("connection was closed by server")
    assert not tasks._matches_transient_db_markers("all good")


def test_is_transient_db_error_wraps_messages() -> None:
    class FakeDBError(Exception):
        def __str__(self):
            return "terminating connection due to administrator command"

    exc = FakeDBError()
    assert tasks._is_transient_db_error(exc) is True


def test_agent_result_indicates_transient_db_error() -> None:
    result = {"error": "Server closed the connection unexpectedly"}
    assert tasks._agent_result_indicates_transient_db_error(result) is True


def test_post_chat_status_to_api_sends_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = {}

    class DummyResponse:
        status_code = 200
        text = ""

    def fake_post(url, json, timeout):
        recorded["url"] = url
        recorded["payload"] = json
        recorded["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(tasks, "requests", SimpleNamespace(post=fake_post))

    tasks._post_chat_status_to_api(
        job_id="job-1",
        agent_key="adam",
        user_id="user-1",
        thread_id="thread-1",
        status="running",
        stage="plan",
        status_message="Working",
        progress=0.5,
        extra={"foo": "bar"},
    )

    assert "job-1" in recorded["payload"]["job_id"]
    assert recorded["payload"]["stage"] == "plan"
    assert recorded["payload"]["extra"] == {"foo": "bar"}
