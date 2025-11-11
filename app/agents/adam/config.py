"""Adam agent configuration helpers."""

from app.sdk.agents.tmates_agents_sdk.config import (
    AgentRuntimeConfig as AdamConfig,
    load_agent_runtime_config,
    normalize_database_url,
)


def load_adam_config() -> AdamConfig:
    return load_agent_runtime_config()


__all__ = ["AdamConfig", "load_adam_config", "normalize_database_url"]
