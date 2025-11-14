"""Tests for filesystem-based agent loading."""

from __future__ import annotations

from app.registry.agents.loader import create_agent, load_agent_class
from app.registry.agents.base import AgentBase
from app.auth import UserContext


def test_load_agent_class_returns_cached_class(monkeypatch) -> None:
    cls_first = load_agent_class("adam")
    cls_second = load_agent_class("adam")

    assert issubclass(cls_first, AgentBase)
    assert cls_first is cls_second


def test_create_agent_injects_user_context() -> None:
    ctx = UserContext(
        user_id="user-1",
        display_name="Tester",
        email="tester@example.com",
        enabled_agents=["adam"],
        agent_configs={"adam": {"mode": "focus"}},
        timezone="UTC",
    )

    agent = create_agent("adam", user_context=ctx)

    assert agent.user_context is ctx
