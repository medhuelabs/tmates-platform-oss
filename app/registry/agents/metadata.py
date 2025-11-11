"""Helpers for loading per-agent configuration defaults."""

from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Any, Dict, Optional

from ...config import INSTALLED_AGENTS


@lru_cache(maxsize=None)
def _load_agent_module(agent_key: str):
    key = (agent_key or "").strip().casefold()
    if not key:
        return None
    if key not in INSTALLED_AGENTS:
        return None
    try:
        return import_module(f"agents.{key}.config")
    except ModuleNotFoundError:
        return None


def get_log_defaults(agent_key: str) -> Dict[str, Any]:
    module = _load_agent_module(agent_key)
    if module and hasattr(module, "LOG_SETTINGS"):
        settings = getattr(module, "LOG_SETTINGS") or {}
        if isinstance(settings, dict):
            return settings
    return {}


def get_agent_icon(agent_key: str) -> Optional[str]:
    module = _load_agent_module(agent_key)
    if module and hasattr(module, "ICON"):
        value = getattr(module, "ICON")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_agent_docs(agent_key: str) -> Optional[str]:
    module = _load_agent_module(agent_key)
    if module and hasattr(module, "DOCS"):
        value = getattr(module, "DOCS")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
