"""Helper utilities for OpenAI service integration."""

from __future__ import annotations

from typing import Any

from app.logger import log as base_log


def log(*parts: Any) -> None:
    """Forward OpenAI service logs through the shared logging sink."""
    base_log(*parts, agent="openai", feed=False)
