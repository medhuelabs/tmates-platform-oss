"""Behavioral tests for the Adam agent scaffolding."""

from __future__ import annotations

import inspect
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/tmates_test.db")

import app.sdk.agents.tmates_agents_sdk.runtime as runtime_module

runtime_module.create_async_engine = lambda url, **_: SimpleNamespace()

from agents import Agent

from app.agents.adam import brain
from app.agents.adam.prompts.brain.loader import load_brain_prompt


def test_brain_exports_agent_and_run_prompt() -> None:
    assert isinstance(brain.agent, Agent)
    assert brain.agent.name == "Adam"
    assert inspect.iscoroutinefunction(brain.run_prompt)


def test_brain_prompt_loader_includes_sections() -> None:
    prompt = load_brain_prompt()

    assert "<system_instructions>" in prompt
    assert "<cognition>" in prompt
    assert "<behavior>" in prompt
