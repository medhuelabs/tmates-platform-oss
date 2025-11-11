"""Environment-driven configuration helpers for TmatesAgentsSDK runtimes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    """Configuration block shared by agent runtimes."""

    env: str
    openai_client: str
    database_url: Optional[str]
    openai_api_key: Optional[str]
    enable_logfire: bool
    logfire_token: Optional[str]
    azure_openai_api_key: Optional[str]
    azure_openai_endpoint: Optional[str]
    azure_openai_api_version: str
    azure_openai_deployment: Optional[str]
    dev_session_id: str = "conversation_123"

    @property
    def database_url_async(self) -> Optional[str]:
        """Return the SQLAlchemy async connection string when available."""

        if not self.database_url:
            return None
        return normalize_database_url(self.database_url)


def load_agent_runtime_config() -> AgentRuntimeConfig:
    """Load configuration from environment variables (with .env support)."""

    return AgentRuntimeConfig(
        env=os.getenv("ENV", "dev").lower(),
        openai_client=os.getenv("OPENAI_CLIENT", "").lower(),
        database_url=os.getenv("DATABASE_URL"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        enable_logfire=os.getenv("ENABLE_LOGFIRE") == "1",
        logfire_token=os.getenv("LOGFIRE_TOKEN"),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview"),
        azure_openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini"),
        dev_session_id=os.getenv("DEV_SESSION_ID", "conversation_123"),
    )


def normalize_database_url(url: str) -> str:
    """Ensure SQLAlchemy uses the asyncpg driver for Postgres URLs."""

    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


__all__ = [
    "AgentRuntimeConfig",
    "load_agent_runtime_config",
    "normalize_database_url",
]
