"""
Agent Registry System

Handles agent discovery, loading, metadata extraction, and management.
"""

from .base import AgentBase
from .models import AgentDefinition
from .repository import AgentRepository
from .store import AgentStore
from .metadata import get_log_defaults, get_agent_icon, get_agent_docs
# Note: loader functions available as registry.agents.loader

__all__ = [
    'AgentBase',
    'AgentDefinition',
    'AgentRepository',
    'AgentStore', 
    'get_log_defaults',
    'get_agent_icon',
    'get_agent_docs',
]