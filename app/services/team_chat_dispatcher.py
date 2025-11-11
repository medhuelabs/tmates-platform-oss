"""
Team chat dispatcher that uses natural language routing to pick a teammate.

This service creates a lightweight OpenAI Agents SDK agent that evaluates the
latest group-chat message, then selects at most one teammate (or declines) for
follow-up processing. The actual teammate execution still happens through the
existing Celery pipeline; the dispatcher only decides who should handle the
message.
"""

from __future__ import annotations

import importlib
import os
import textwrap
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import logfire
from agents import (
    Agent,
    ModelSettings,
    Runner,
    handoff,
    set_tracing_export_api_key,
)
from agents.exceptions import MaxTurnsExceeded
from openai.types.shared import Reasoning

from app.registry.agents.models import AgentDefinition
from app.registry.agents.store import AgentStore

try:  # pragma: no cover - typed import for local runs where logging is configured
    from logs import log
except Exception:  # noqa: BLE001

    def log(message: str) -> None:  # type: ignore[override]
        print(message)


DECLINE_TOKEN = "DECLINE"
TEAM_CHAT_DISPATCHER_MODEL = os.getenv(
    "TEAM_CHAT_DISPATCHER_MODEL",
    os.getenv("OPENAI_MODEL", "gpt-5-mini"),
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
_LOGFIRE_CONFIGURED = False
_TRACING_CONFIGURED = False

set_tracing_export_api_key(OPENAI_API_KEY)

def _is_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configure_observability() -> None:
    global _LOGFIRE_CONFIGURED, _TRACING_CONFIGURED

    if not _LOGFIRE_CONFIGURED:
        enable_value = os.getenv("TEAM_CHAT_ENABLE_LOGFIRE")
        if enable_value is None:
            enable_value = os.getenv("ENABLE_LOGFIRE")
        if _is_truthy(enable_value):
            token = os.getenv("TEAM_CHAT_LOGFIRE_TOKEN") or os.getenv("LOGFIRE_TOKEN")
            if token:
                try:
                    logfire.configure(token=token, console=False)
                    logfire.instrument_openai_agents()
                    _LOGFIRE_CONFIGURED = True
                except Exception as exc:  # pragma: no cover - defensive observability setup
                    log(f"[dispatcher] Failed to configure Logfire: {exc}")

    if not _TRACING_CONFIGURED:
        api_key = os.getenv("TEAM_CHAT_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if api_key:
            try:
                set_tracing_export_api_key(api_key)
                _TRACING_CONFIGURED = True
            except Exception as exc:  # pragma: no cover - defensive observability setup
                log(f"[dispatcher] Failed to configure OpenAI tracing: {exc}")


class DispatcherSelection(Exception):
    """Raised when the dispatcher chooses a teammate handoff."""

    def __init__(self, agent_key: str) -> None:
        super().__init__(f"Dispatcher selected agent '{agent_key}'")
        self.agent_key = agent_key


@dataclass(slots=True)
class TeamDispatchResult:
    """Outcome produced by the dispatcher."""

    selected_agent_key: Optional[str]
    declined: bool = False
    output_text: Optional[str] = None
    error: Optional[str] = None


def _coerce_text(value: Any, fallback: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return fallback
    return str(value)


def _sort_messages_by_created_at(
    messages: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return messages ordered by their created_at timestamp (ascending)."""
    indexed = list(enumerate(messages))

    def _sort_key(item: Tuple[int, Dict[str, Any]]) -> Tuple[str, int]:
        _, payload = item
        timestamp = _coerce_text(payload.get("created_at"), "")
        return timestamp, item[0]

    indexed.sort(key=_sort_key)
    return [entry for _, entry in indexed]


class TeamChatDispatcher:
    """Natural-language router for Team Chat messages."""

    def __init__(self) -> None:
        _configure_observability()
        self._store = AgentStore()
        self._agents_cache: Dict[str, Agent] = {}
        self._definitions_cache: Dict[str, AgentDefinition] = {}

    @staticmethod
    def _normalize_label(value: Any) -> Optional[str]:
        text = _coerce_text(value)
        text = text.strip()
        return text or None

    def _identify_last_teammate(
        self, messages: Sequence[Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return the last teammate author and agent_key from the history."""

        for entry in reversed(messages):
            role = self._normalize_label(entry.get("role"))
            if (role or "").lower() != "assistant":
                continue

            payload = entry.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            agent_key = self._normalize_label(payload.get("agent_key"))
            author = (
                self._normalize_label(entry.get("author"))
                or self._normalize_label(payload.get("agent_name"))
            )

            if not author and agent_key:
                author = agent_key

            if author or agent_key:
                return author, agent_key

        return None, None

    def _get_definition(self, agent_key: str) -> Optional[AgentDefinition]:
        if agent_key in self._definitions_cache:
            return self._definitions_cache[agent_key]
        definition = self._store.get_agent(agent_key)
        if definition:
            self._definitions_cache[agent_key] = definition
        return definition

    def _load_agent_instance(self, agent_key: str) -> Optional[Agent]:
        if agent_key in self._agents_cache:
            return self._agents_cache[agent_key]
        try:
            module = importlib.import_module(f"app.agents.{agent_key}.brain")
        except ModuleNotFoundError:
            return None
        candidate = getattr(module, "agent", None)
        if isinstance(candidate, Agent):
            self._agents_cache[agent_key] = candidate
            return candidate
        return None

    def _build_conversation_excerpt(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        limit: int = 8,
    ) -> str:
        """Return a compact textual history for prompt conditioning."""

        lines: List[str] = []
        history = _sort_messages_by_created_at(messages)
        if limit > 0:
            history = history[-limit:]

        for entry in history:
            role = _coerce_text(entry.get("role"), "assistant")
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            author = _coerce_text(entry.get("author"), payload.get("agent_key", role))
            content = _coerce_text(entry.get("content")).strip()
            if not content:
                continue
            snippet = content if len(content) <= 500 else f"{content[:500]}…"
            lines.append(f"{author} ({role}): {snippet}")

        if not lines:
            return "The conversation has no earlier messages."
        return "\n".join(lines)

    def _build_dispatcher_agent(
        self,
        roster: Sequence[Tuple[str, Agent, AgentDefinition]],
        *,
        last_agent_label: Optional[str] = None,
    ) -> Agent:
        """Create a fresh dispatcher agent with per-run handoff callbacks."""

        handoffs_list = []

        for agent_key, agent_instance, definition in roster:

            def _make_on_handoff(key: str):
                def _on_handoff(_ctx):
                    raise DispatcherSelection(key)

                return _on_handoff

            handoffs_list.append(
                handoff(
                    agent=agent_instance,
                    on_handoff=_make_on_handoff(agent_key),
                )
            )

        decline_agent = Agent(
            name="No teammate",
            instructions=(
                "Return the single token DECLINE when no teammate is an appropriate match."
            ),
            model=TEAM_CHAT_DISPATCHER_MODEL,
        )
        handoffs_list.append(
            handoff(
                agent=decline_agent,
                tool_name_override="decline",
                tool_description_override="Choose when no teammate should respond.",
                on_handoff=lambda _ctx: (_raise_dispatch_decline()),
            )
        )

        roster_lines = []
        for agent_key, agent_instance, definition in roster:
            handoff_description = getattr(agent_instance, "handoff_description", None)
            description = handoff_description or definition.description or ""
            roster_lines.append(
                f"- {definition.name} (`{agent_key}`): {description}".strip()
            )

        roster_block = "\n".join(roster_lines) or "None provided."

        paragraphs: List[str] = [
            "You are the moderator of the AI team group chat. You are the responsible to decide which agent should respond next based on the latest user message and recent context.",
        ]
        paragraphs.append(
            "The members of the group chat are the following teammates:\n"
            + textwrap.indent(roster_block, "  ")
        )
        paragraphs.append(
            "INSTRUCTIONS:"
            "1. Read the recent messages and the latest user message carefully.\n"
            "2. Identify if the user is calling for a specific teammate in the group chat. If that is the case, handoff the conversation to that teammate. Else, continue.\n"
            "3. Evaluate if the last message is a follow-up to a previous teammate's response. If so, handoff the conversation to that teammate. Else, continue.\n"
            "4. Identify if the last message introduces a new topic. If so, select the most suitable teammate from the available agents, based on their expertise, handoff_description and the context of the conversation.\n"
            "5. If none of the teammates fit, call the decline tool."
        )
        instructions = "\n\n".join(paragraphs).strip()

        return Agent(
            name="Team Chat Dispatcher",
            instructions=instructions,
            model=TEAM_CHAT_DISPATCHER_MODEL,
            model_settings=ModelSettings(reasoning=Reasoning(effort="medium"), verbosity="low"),
            handoffs=handoffs_list,
        )

    async def dispatch(
        self,
        *,
        message_text: str,
        enabled_agent_keys: Sequence[str],
        thread_title: str,
        messages: Sequence[Dict[str, Any]],
    ) -> TeamDispatchResult:
        """
        Determine which teammate should answer the latest Team Chat message.

        Args:
            message_text: The freshly submitted user message.
            enabled_agent_keys: Teammate keys permitted in this chat.
            thread_title: Human-readable thread title for context.
            messages: Recent chat history (ascending order).
        """

        roster: List[Tuple[str, Agent, AgentDefinition]] = []
        for agent_key in enabled_agent_keys:
            agent_instance = self._load_agent_instance(agent_key)
            definition = self._get_definition(agent_key)
            if not agent_instance or not definition:
                continue
            roster.append((agent_key, agent_instance, definition))

        if not roster:
            return TeamDispatchResult(
                selected_agent_key=None,
                declined=True,
                output_text="No eligible teammates are available.",
            )

        last_author_name, last_agent_key = self._identify_last_teammate(messages)
        last_agent_label = None
        if last_agent_key:
            for key, _, definition in roster:
                if key == last_agent_key:
                    last_agent_label = definition.name
                    break
        if not last_agent_label and last_author_name:
            last_agent_label = last_author_name
        if not last_agent_label and last_agent_key:
            last_agent_label = last_agent_key

        dispatcher_agent = self._build_dispatcher_agent(
            roster, last_agent_label=last_agent_label
        )
        conversation_excerpt = self._build_conversation_excerpt(messages)

        prompt_sections: List[str] = []
        prompt_sections.append(
            "Recent messages:\n" + textwrap.indent(conversation_excerpt, "  ")
        )
        prompt_sections.append(
            "User message:\n"
            + textwrap.indent(message_text.strip() or "[empty]", "  ")
        )
        prompt = "\n\n".join(prompt_sections).strip()

        try:
            result = await Runner.run(dispatcher_agent, prompt, max_turns=1)
        except DispatcherSelection as selection:
            return TeamDispatchResult(
                selected_agent_key=selection.agent_key,
                declined=False,
            )
        except DispatcherDecline:
            return TeamDispatchResult(
                selected_agent_key=None,
                declined=True,
                output_text="Dispatcher declined to assign a teammate.",
            )
        except MaxTurnsExceeded:
            log("[dispatcher] Max turns exceeded while selecting teammate.")
            return TeamDispatchResult(
                selected_agent_key=None,
                declined=False,
                error="dispatcher_max_turns",
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            log(f"[dispatcher] Unexpected error: {exc}")
            return TeamDispatchResult(
                selected_agent_key=None,
                declined=False,
                error=str(exc),
            )

        final_output = _coerce_text(getattr(result, "final_output", "")).strip()
        if final_output.upper() == DECLINE_TOKEN:
            return TeamDispatchResult(
                selected_agent_key=None,
                declined=True,
                output_text=final_output,
            )

        return TeamDispatchResult(
            selected_agent_key=None,
            declined=False,
            output_text=final_output or None,
        )


class DispatcherDecline(Exception):
    """Raised when the dispatcher explicitly chooses to decline."""


def _raise_dispatch_decline() -> None:
    raise DispatcherDecline("Dispatcher declined to pick a teammate.")


# Global instance
team_chat_dispatcher = TeamChatDispatcher()
