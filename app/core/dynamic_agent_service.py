"""
Dynamic Agent Service - Provides agent metadata dynamically from registry

This replaces hard-coded agent definitions with dynamic discovery from the agent registry.
"""

from typing import Dict, List, Optional, Any
from app.registry.agents.repository import AgentRepository


class DynamicAgentService:
    """
    Service for dynamically discovering and providing agent metadata.
    
    Handles two distinct layers:
    1. PLATFORM LEVEL: Agents installed/available on the backend (INSTALLED_AGENTS)
    2. USER LEVEL: Agents the user has enabled/purchased for their account
    """
    
    def __init__(self):
        self.agent_repo = AgentRepository()
        # Remove database dependency to avoid circular imports
    
    def get_all_available_agent_keys(self) -> List[str]:
        """
        Get all agent keys available at the PLATFORM LEVEL.
        These are agents installed on the backend via INSTALLED_AGENTS config.
        """
        return list(self.agent_repo.keys())
    
    def get_enabled_agents_for_user(self, user_context) -> List[str]:
        """
        Get agents enabled at the USER LEVEL.
        These are agents the user has purchased/enabled for their account.
        This is a subset of available platform agents.
        """
        if hasattr(user_context, 'enabled_agents') and user_context.enabled_agents:
            return list(user_context.enabled_agents)
        
        # Fallback: return all available platform agents
        return self.get_all_available_agent_keys()
    
    def get_agent_access_status(self, agent_key: str, user_context) -> Dict[str, Any]:
        """
        Get comprehensive access status for an agent across both layers.
        
        Returns:
        - platform_available: Is the agent installed on the backend?
        - user_enabled: Has the user enabled/purchased this agent?
        - can_use: Can the user actually use this agent?
        """
        available_agents = self.get_all_available_agent_keys()
        enabled_agents = self.get_enabled_agents_for_user(user_context)
        
        platform_available = agent_key in available_agents
        user_enabled = agent_key in enabled_agents
        can_use = platform_available and user_enabled
        
        return {
            'agent_key': agent_key,
            'platform_available': platform_available,
            'user_enabled': user_enabled,
            'can_use': can_use,
            'status': 'available' if can_use else 
                     'not_enabled' if platform_available else 
                     'not_installed'
        }
    
    def get_agent_metadata(self, agent_key: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific agent from registry."""
        definition = self.agent_repo.get(agent_key)
        if not definition:
            return None
        
        manifest: Dict[str, Any] = definition.manifest or {}
        manifest_branding = manifest.get("branding") if isinstance(manifest.get("branding"), dict) else None
        manifest_ui = manifest.get("ui") if isinstance(manifest.get("ui"), dict) else None

        metadata: Dict[str, Any] = {
            'key': agent_key,
            'name': definition.name or agent_key.title(),
            'description': definition.description or f"{agent_key.title()} Agent",
            'icon': definition.icon,
            'docs': definition.docs,
        }
        
        if manifest:
            metadata['manifest'] = manifest
        if manifest_branding:
            metadata['branding'] = manifest_branding
        if manifest_ui:
            metadata['ui'] = manifest_ui
            settings_block = manifest_ui.get("settings")
            if isinstance(settings_block, dict):
                metadata['settings'] = settings_block
        
        return metadata
    
    def get_all_agents_metadata(self, user_context=None) -> Dict[str, Dict[str, Any]]:
        """
        Get metadata for all available agents with access status.
        
        For each agent, includes:
        - Basic metadata (name, description, etc.)
        - Platform availability status
        - User access status (if user_context provided)
        """
        metadata = {}
        available_agents = self.get_all_available_agent_keys()
        
        for agent_key in available_agents:
            agent_metadata = self.get_agent_metadata(agent_key)
            if agent_metadata:
                # Add access status if user context is available
                if user_context:
                    access_status = self.get_agent_access_status(agent_key, user_context)
                    agent_metadata.update(access_status)
                
                metadata[agent_key] = agent_metadata
                
        return metadata
    
    def get_agent_display_name(self, agent_key: str) -> str:
        """Get display name for an agent (with fallback)."""
        metadata = self.get_agent_metadata(agent_key)
        if metadata:
            return metadata['name']
        return agent_key.title()
    
    def get_agent_role(self, agent_key: str) -> str:
        """Get role/description for an agent (with fallback)."""
        metadata = self.get_agent_metadata(agent_key)
        if metadata and metadata['description']:
            return metadata['description']
        return f"{agent_key.title()} Agent"
    
    def is_agent_available_on_platform(self, agent_key: str) -> bool:
        """Check if an agent is installed/available on the platform."""
        return agent_key in self.get_all_available_agent_keys()
    
    def is_agent_enabled_for_user(self, agent_key: str, user_context) -> bool:
        """Check if an agent is enabled for a specific user."""
        enabled_agents = self.get_enabled_agents_for_user(user_context)
        return agent_key in enabled_agents
    
    def can_user_access_agent(self, agent_key: str, user_context) -> bool:
        """Check if user can actually use an agent (both platform available AND user enabled)."""
        return (self.is_agent_available_on_platform(agent_key) and 
                self.is_agent_enabled_for_user(agent_key, user_context))

    # Backward compatibility methods (deprecated)
    def get_all_agent_keys(self) -> List[str]:
        """DEPRECATED: Use get_all_available_agent_keys() instead."""
        return self.get_all_available_agent_keys()
    
    def is_valid_agent(self, agent_key: str) -> bool:
        """DEPRECATED: Use is_agent_available_on_platform() instead."""
        return self.is_agent_available_on_platform(agent_key)


# Global instance
dynamic_agent_service = DynamicAgentService()
