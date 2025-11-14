"""Tests for shared FastAPI dependency helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import dependencies


def test_get_current_user_id_requires_header() -> None:
    with pytest.raises(HTTPException) as exc:
        dependencies.get_current_user_id(authorization=None)

    assert exc.value.status_code == 401


def test_get_current_user_id_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "require_auth", lambda header: "user-123")

    result = dependencies.get_current_user_id("Bearer abc")
    assert result == "user-123"


def test_get_authenticated_user_rejects_missing_bearer_prefix() -> None:
    with pytest.raises(HTTPException) as exc:
        dependencies.get_authenticated_user("Token abc")

    assert exc.value.status_code == 401


def test_get_authenticated_user_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubAuthManager:
        def get_user_from_token(self, token: str):
            assert token == "abc"
            return {"id": "user-1", "email": "u@example.com", "metadata": {"plan": "pro"}}

    monkeypatch.setattr(dependencies, "get_auth_manager", lambda: StubAuthManager())

    result = dependencies.get_authenticated_user("Bearer abc")
    assert result == {"id": "user-1", "email": "u@example.com", "metadata": {"plan": "pro"}}
