"""Tests for shared Pinboard helpers."""

from __future__ import annotations

from types import SimpleNamespace

from app.tools import pinboard


def test_dump_models_serializes_base_models() -> None:
    attachment = pinboard.PinboardAttachmentInput(url="https://example.com/file.pdf", label="File")
    dumped = pinboard._dump_models([attachment])

    assert dumped == [{"url": "https://example.com/file.pdf", "label": "File"}]


def test_extract_user_id_prefers_context(monkeypatch) -> None:
    ctx = SimpleNamespace(user_id="user-99")
    assert pinboard._extract_user_id(ctx) == "user-99"

    ctx = SimpleNamespace(user_id=None, context={"user_id": "ctx-user"})
    assert pinboard._extract_user_id(ctx) == "ctx-user"


def test_extract_user_id_falls_back_to_env(monkeypatch) -> None:
    ctx = SimpleNamespace()
    monkeypatch.setenv("USER_ID", "env-user")
    assert pinboard._extract_user_id(ctx) == "env-user"
