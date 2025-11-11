"""Shared type definitions for the TmatesAgentsSDK."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol


class RunPromptCallable(Protocol):
    async def __call__(
        self,
        prompt: str,
        user_id: Optional[str],
        session_id: Optional[str],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Execute the agent brain asynchronously."""


ContextBuilder = Callable[[Dict[str, Any], str, str], Optional[Dict[str, Any]]]


__all__ = [
    "RunPromptCallable",
    "ContextBuilder",
]
