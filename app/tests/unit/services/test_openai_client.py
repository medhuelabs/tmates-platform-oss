"""Tests for OpenAI client helpers."""

from __future__ import annotations

import types

import pytest

from app.services.openai import client as client_module


def _reset_client_state():
    client_module._client = None
    client_module._client_is_azure = False
    client_module._azure_deployment = None


def test_supports_temperature_flags_gpt5_models() -> None:
    assert client_module._supports_temperature("gpt-5") is False
    assert client_module._supports_temperature("gpt-4o") is True


def test_openai_client_prefers_standard_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_client_state()
    monkeypatch.setenv("OPENAI_CLIENT", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    created = {}

    class DummyOpenAI:
        def __init__(self, *, api_key):
            created["api_key"] = api_key

    monkeypatch.setattr(client_module, "OpenAI", DummyOpenAI)

    obj = client_module.openai_client()

    assert isinstance(obj, DummyOpenAI)
    assert created["api_key"] == "test-key"


def test_openai_client_initializes_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_client_state()
    monkeypatch.setenv("OPENAI_CLIENT", "azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    created = {}

    class DummyAzure:
        def __init__(self, *, azure_endpoint, api_key, api_version):
            created["endpoint"] = azure_endpoint
            created["api_key"] = api_key
            created["api_version"] = api_version

    monkeypatch.setattr(client_module, "AzureOpenAI", DummyAzure)

    obj = client_module.openai_client()

    assert isinstance(obj, DummyAzure)
    assert created["endpoint"] == "https://example.com"
    assert created["api_key"] == "azure-key"
    _reset_client_state()
