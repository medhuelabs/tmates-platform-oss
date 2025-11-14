"""Smoke tests for Dana's brain module."""

from __future__ import annotations

import inspect

from agents import Agent

from app.agents.dana import brain
from app.agents.dana.prompts.brain.loader import load_brain_prompt


def test_dana_brain_exports_agent_and_run_prompt() -> None:
    assert isinstance(brain.agent, Agent)
    assert brain.agent.name == "Dana"
    assert inspect.iscoroutinefunction(brain.run_prompt)


def test_dana_brain_registers_expected_tools() -> None:
    tool_names = {tool.name for tool in brain.agent.tools}
    expected = {
        "request_gmail_login_link",
        "gmail_connection_status",
        "gmail_search_messages",
        "gmail_read_message",
        "gmail_send_email",
        "create_pinboard_post_tool",
    }
    assert expected.issubset(tool_names)


def test_dana_prompt_loader_includes_sections() -> None:
    prompt = load_brain_prompt()
    for tag in ("<system_instructions>", "<cognition>", "<behavior>"):
        assert tag in prompt
