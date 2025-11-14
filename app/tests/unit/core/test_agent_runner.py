"""Unit tests for helpers inside app.core.agent_runner."""

from __future__ import annotations

import os

import pytest

from app.core.agent_runner import _parse_cancel_command, apply_user_context_to_env
from app.auth import UserContext


def test_parse_cancel_command_detects_exact_keyword() -> None:
    assert _parse_cancel_command("stop") == (True, None)
    assert _parse_cancel_command("cancel") == (True, None)


def test_parse_cancel_command_extracts_target() -> None:
    assert _parse_cancel_command("cancel adam") == (True, "adam")
    assert _parse_cancel_command("Stop task-42") == (True, "task-42")


@pytest.mark.parametrize("message", ["", "hello", "cancelled", " status "])
def test_parse_cancel_command_non_matches(message: str) -> None:
    assert _parse_cancel_command(message) == (False, None)


def test_apply_user_context_to_env_sets_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    user_ctx = UserContext(
        user_id="abc-123",
        display_name="Example",
        email="ex@example.com",
        enabled_agents=["adam"],
        agent_configs={"adam": {"mode": "fast"}},
        timezone="Europe/Berlin",
    )

    for key in (
        "USER_CONTEXT_USER_ID",
        "USER_CONTEXT_DISPLAY_NAME",
        "USER_ID",
        "USER_DISPLAY_NAME",
        "USER_EMAIL",
        "USER_TIMEZONE",
        "ENABLED_AGENTS",
        "AGENT_CONFIGS",
    ):
        monkeypatch.delenv(key, raising=False)

    apply_user_context_to_env(user_ctx)

    assert os.environ["USER_CONTEXT_USER_ID"] == "abc-123"
    assert os.environ["USER_DISPLAY_NAME"] == "Example"
    assert os.environ["USER_EMAIL"] == "ex@example.com"
    assert os.environ["USER_TIMEZONE"] == "Europe/Berlin"
    assert os.environ["ENABLED_AGENTS"] == '["adam"]'
    assert os.environ["AGENT_CONFIGS"] == '{"adam": {"mode": "fast"}}'
