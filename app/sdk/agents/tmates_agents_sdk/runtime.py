"""Shared runtime wrapper for building tmates agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

import logfire
from agents import (
    Agent,
    ModelSettings,
    RunConfig,
    Runner,
    set_default_openai_client,
    set_tracing_export_api_key,
)
from agents.extensions.memory import SQLAlchemySession
from openai import AsyncAzureOpenAI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.sdk.agents.tmates_agents_sdk.config import AgentRuntimeConfig, load_agent_runtime_config

InstructionsLoader = Callable[[], str]
ToolIterable = Iterable[object]


@dataclass
class TmatesAgentsSDK:
    """Bootstrap helper that wires configuration, memory, and the Agent object."""

    agent_key: str
    name: str
    handoff_description: str
    instructions_loader: InstructionsLoader
    model: str
    model_settings: ModelSettings
    tools: ToolIterable = field(default_factory=list)
    config: Optional[AgentRuntimeConfig] = None

    def __post_init__(self) -> None:
        self.config = self.config or load_agent_runtime_config()
        self._configure_logfire()
        self._configure_openai_client()
        self.engine: AsyncEngine = self._create_engine()
        self.agent = Agent(
            name=self.name,
            handoff_description=self.handoff_description,
            instructions=self.instructions_loader(),
            model=self.model,
            model_settings=self.model_settings,
            tools=list(self.tools),
        )
        self._session_cache: dict[str, SQLAlchemySession] = {}

    # --- configuration helpers -------------------------------------------------
    def _configure_logfire(self) -> None:
        cfg = self.config
        if cfg and cfg.enable_logfire and cfg.logfire_token:
            logfire.configure(token=cfg.logfire_token, console=False)
            logfire.instrument_openai_agents()

    def _configure_openai_client(self) -> None:
        cfg = self.config
        if not cfg:
            return

        if cfg.openai_client == "azure" and cfg.azure_openai_api_key and cfg.azure_openai_endpoint:
            client = AsyncAzureOpenAI(
                api_version=cfg.azure_openai_api_version,
                azure_deployment=cfg.azure_openai_deployment,
                api_key=cfg.azure_openai_api_key,
                azure_endpoint=cfg.azure_openai_endpoint,
            )
            set_default_openai_client(client)

        if cfg.openai_api_key:
            set_tracing_export_api_key(cfg.openai_api_key)

    def _create_engine(self) -> AsyncEngine:
        cfg = self.config
        if not cfg or not cfg.database_url_async:
            raise ValueError("DATABASE_URL environment variable is required for agent memory")
        return create_async_engine(cfg.database_url_async)

    # --- session helpers -------------------------------------------------------
    def _session_identifier(self, user_id: Optional[str], session_id: Optional[str]) -> str:
        candidate = session_id or user_id or (self.config.dev_session_id if self.config else None)
        if not candidate:
            raise ValueError("session identifier is required")
        return candidate

    def _get_session(self, user_id: Optional[str], session_id: Optional[str]) -> SQLAlchemySession:
        identifier = self._session_identifier(user_id, session_id)
        session = self._session_cache.get(identifier)
        if session is None:
            session = SQLAlchemySession(identifier, engine=self.engine, create_tables=True)
            self._session_cache[identifier] = session
        return session

    # --- public API ------------------------------------------------------------
    async def run_prompt(
        self,
        prompt: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        *,
        context: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Invoke the shared agent asynchronously with memory support."""

        session = self._get_session(user_id, session_id)
        agent_input: str | list[Dict[str, Any]]
        attachment_items: List[Dict[str, Any]] = []
        run_config: RunConfig | None = None
        if attachments:
            for item in attachments:
                if isinstance(item, dict) and item.get("type") == "input_image" and item.get("image_url"):
                    attachment_items.append(dict(item))

        if attachment_items:
            content_parts: List[Dict[str, Any]] = []
            if isinstance(prompt, str) and prompt.strip():
                content_parts.append({"type": "input_text", "text": prompt})
            content_parts.extend(attachment_items)
            agent_input = [{"role": "user", "content": content_parts}]
            run_config = RunConfig(
                session_input_callback=lambda _previous, new_input: new_input,
            )
        else:
            agent_input = prompt

        response = await Runner.run(
            self.agent,
            agent_input,
            session=session,
            context=context,
            run_config=run_config,
        )
        return response.final_output


__all__ = ["TmatesAgentsSDK"]
