"""
Registry System for Agents and Tools

This module provides:
- Agent discovery and registration (via registry.agents)
- Tool capability registration (via registry.tools)
- Dynamic loading of agent classes
- Agent store for user management
- Tool routing for task distribution
- Agent metadata (icons, docs, settings)
"""

# Import commonly used items from submodules for convenience
from .agents import (
    AgentBase,
    AgentDefinition,
    AgentRepository,
    AgentStore,
    get_log_defaults,
    get_agent_icon,
    get_agent_docs,
)
from .tools import ToolDefinition

# Submodules are also available as registry.agents and registry.tools
from . import agents
from . import tools

__all__ = [
    # Agent registry
    'AgentBase',
    'AgentDefinition',
    'AgentRepository', 
    'AgentStore',
    'get_log_defaults',
    'get_agent_icon', 
    'get_agent_docs',
    # Tool registry
    'ToolDefinition',
    # Submodules
    'agents',
    'tools',
]