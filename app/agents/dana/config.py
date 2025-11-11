"""Configuration helpers shared by the Dana agent."""

from __future__ import annotations

import os

from app.agents.adam.config import AdamConfig, load_adam_config, normalize_database_url


DEFAULT_MODEL = os.getenv("DANA_MODEL", "gpt-5-mini")
DOCS = (
    "Dana is a focused teammate that connects to Gmail on behalf of each tmates user. "
    "It can search, read, summarise, and compose messages once the user grants access via Google OAuth. "
    "Dana respects per-user credentials, keeps conversations scoped to the requesting user, "
    "and never stores message bodies outside the session context."
)
ICON = "📧"

__all__ = [
    "AdamConfig",
    "DEFAULT_MODEL",
    "DOCS",
    "ICON",
    "load_adam_config",
    "normalize_database_url",
]
