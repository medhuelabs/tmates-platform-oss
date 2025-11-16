"""Tests for profile routes."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import profile as profile_routes


class StubDB:
    def __init__(self, *, should_succeed: bool = True):
        self.should_succeed = should_succeed
        self.deleted_users: list[str] = []

    def delete_user_account(self, user_id: str) -> bool:
        self.deleted_users.append(user_id)
        return self.should_succeed


class StubAuthManager:
    def __init__(self, *, should_succeed: bool = True):
        self.should_succeed = should_succeed
        self.deleted_auth_ids: list[str] = []

    def delete_auth_user(self, user_id: str) -> bool:
        self.deleted_auth_ids.append(user_id)
        return self.should_succeed


def test_delete_profile_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = StubDB(should_succeed=True)
    auth = StubAuthManager(should_succeed=True)
    monkeypatch.setattr(profile_routes, "get_auth_manager", lambda: auth)

    response = profile_routes.delete_profile(context=("user-1", db))

    assert response.status == "deleted"
    assert db.deleted_users == ["user-1"]
    assert auth.deleted_auth_ids == ["user-1"]


def test_delete_profile_database_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = StubDB(should_succeed=False)
    auth = StubAuthManager(should_succeed=True)
    monkeypatch.setattr(profile_routes, "get_auth_manager", lambda: auth)

    with pytest.raises(HTTPException) as exc:
        profile_routes.delete_profile(context=("user-2", db))

    assert exc.value.status_code == 500
    assert auth.deleted_auth_ids == []


def test_delete_profile_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = StubDB(should_succeed=True)
    auth = StubAuthManager(should_succeed=False)
    monkeypatch.setattr(profile_routes, "get_auth_manager", lambda: auth)

    with pytest.raises(HTTPException) as exc:
        profile_routes.delete_profile(context=("user-3", db))

    assert exc.value.status_code == 502
    assert db.deleted_users == ["user-3"]
    assert auth.deleted_auth_ids == ["user-3"]
