"""Base classes and constants for agent plugins."""

from __future__ import annotations

import sys
import uuid
from abc import ABC
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from app.auth import UserContext


class AgentBase(ABC):
    """Base class for automation agents."""

    key: str

    def __init__(self, user_context: Optional["UserContext"] = None) -> None:
        self.user_context = user_context

    @property
    def agent_key(self) -> str:
        return getattr(self, "key", self.__class__.__name__.replace("Agent", "").casefold())

    def run(
        self,
        *,
        cli_args: Optional[Dict[str, Any]] = None,
        extra_args: Optional[List[str]] = None,
    ) -> int:
        """Execute the agent synchronously through the API pathway."""

        api_request = self._build_api_request(cli_args or {}, extra_args or [])
        response = self.run_api(api_request)

        success = bool(response.get("success", True)) if isinstance(response, dict) else False
        exit_code = response.get("exit_code") if isinstance(response, dict) else None
        response_text = ""
        error_text = ""

        if isinstance(response, dict):
            response_text = str(response.get("response") or "")
            error_text = str(response.get("error") or "")

        if success:
            if response_text:
                print(response_text)
        else:
            message = error_text or f"Agent '{self.agent_key}' reported an error."
            print(message, file=sys.stderr)

        if isinstance(exit_code, int):
            return exit_code
        return 0 if success else 1

    def run_api(self, request: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def _build_api_request(self, cli_args: Dict[str, Any], extra_args: List[str]) -> Dict[str, Any]:
        """Translate historical CLI arguments into an API-style payload."""

        message = cli_args.get("message") or cli_args.get("prompt") or ""
        author = cli_args.get("author") or "CLI"
        thread_id = cli_args.get("thread_id")
        session_id = cli_args.get("session_id") or f"cli-{uuid.uuid4().hex}"

        metadata: Dict[str, Any] = {}
        if isinstance(cli_args.get("metadata"), dict):
            metadata.update(cli_args["metadata"])

        attachments = cli_args.get("attachments")
        if attachments is not None:
            metadata.setdefault("attachments", attachments)

        recognised = {"message", "prompt", "author", "session_id", "thread_id", "metadata", "attachments"}
        for key, value in cli_args.items():
            if key not in recognised:
                metadata.setdefault(key, value)

        if extra_args:
            metadata.setdefault("extra_args", list(extra_args))

        api_request: Dict[str, Any] = {
            "message": str(message),
            "author": str(author),
            "session_id": str(session_id),
        }

        if thread_id is not None:
            api_request["thread_id"] = thread_id

        if metadata:
            api_request["metadata"] = metadata

        return api_request
