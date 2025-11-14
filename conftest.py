"""Repository-wide pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest

from app.auth import UserContext


@pytest.fixture(autouse=True)
def _set_default_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Generator[None, None, None]:
    """Ensure agent modules can instantiate their runtimes during import."""

    db_path = tmp_path / "tmates_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("OPENAI_CLIENT", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    yield


@pytest.fixture
def user_context() -> UserContext:
    """Reusable user context fixture."""

    return UserContext(
        user_id="user-123",
        display_name="Test User",
        email="test@example.com",
        enabled_agents=["adam"],
        agent_configs={"adam": {"foo": "bar"}},
        timezone="UTC",
    )
