"""Smoke tests for Nolan's brain module."""

from __future__ import annotations

import inspect

from agents import Agent

from app.agents.nolan import brain
from app.agents.nolan.prompts.brain.loader import load_brain_prompt


def test_nolan_brain_exports_agent_and_run_prompt() -> None:
    assert isinstance(brain.agent, Agent)
    assert brain.agent.name == "Nolan"
    assert inspect.iscoroutinefunction(brain.run_prompt)


def test_nolan_brain_registers_expected_tools() -> None:
    tool_names = {tool.name for tool in brain.agent.tools}
    expected = {"generate_video", "remix_video", "announce_plan", "create_pinboard_post_tool"}
    assert expected.issubset(tool_names)


def test_nolan_prompt_loader_includes_sections() -> None:
    prompt = load_brain_prompt()
    for tag in ("<system_instructions>", "<cognition>", "<behavior>"):
        assert tag in prompt
