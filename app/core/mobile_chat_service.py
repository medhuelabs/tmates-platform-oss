"""
Mobile-First Chat Architecture - Replaces Web UI ChatManager

This module provides clean, mobile-first chat management without the complexity
and architectural conflicts of the web-oriented ChatManager system.
"""

from typing import List, Dict, Any, Optional
from app.db import TransientDatabaseError, get_database_client
from app.auth import UserContext


class MobileChatService:
    """
    Clean mobile-first chat service that replaces ChatManager.
    
    Key differences from ChatManager:
    - No automatic thread manipulation
    - Respects existing mobile configurations
    - Simpler, more predictable behavior
    - No web UI dependencies
    """
    
    def __init__(self):
        self.db = get_database_client()
    
    def get_or_create_individual_thread(self, user_context: UserContext, agent_name: str) -> Dict[str, Any]:
        """
        Get or create an individual agent thread with proper mobile configuration.
        """
        # Look for existing thread
        threads = self.db.list_chat_threads(
            user_context.user_id,
            organization_id=user_context.organization_id
        )
        
        for thread in threads:
            metadata = thread.get('metadata', {})
            if metadata.get('slug') == f'agent:{agent_name}':
                # Ensure it has the correct agent_keys
                if not metadata.get('agent_keys'):
                    self._fix_thread_agent_keys(thread['id'], [agent_name])
                return thread
        
        # Create new thread
        return self._create_individual_thread(user_context, agent_name)
    
    def get_or_create_group_thread(self, user_context: UserContext) -> Dict[str, Any]:
        """
        Get or create group chat thread with all enabled agents.
        """
        threads = self.db.list_chat_threads(
            user_context.user_id,
            organization_id=user_context.organization_id
        )
        
        for thread in threads:
            metadata = thread.get('metadata', {})
            if metadata.get('slug') == 'group:all':
                # Ensure it has all enabled agents
                enabled_agents = list(user_context.enabled_agents or [])
                if set(metadata.get('agent_keys', [])) != set(enabled_agents):
                    self._fix_thread_agent_keys(thread['id'], enabled_agents)
                return thread
        
        # Create new thread
        return self._create_group_thread(user_context)
    
    def _create_individual_thread(self, user_context: UserContext, agent_name: str) -> Dict[str, Any]:
        """Create a new individual agent thread with proper configuration."""
        thread_data = {
            'title': agent_name.title(),
            'metadata': {
                'slug': f'agent:{agent_name}',
                'source': 'mobile',
                'agent_keys': [agent_name]
            }
        }
        
        return self.db.create_chat_thread(
            user_id=user_context.user_id,
            organization_id=user_context.organization_id,
            **thread_data
        )
    
    def _create_group_thread(self, user_context: UserContext) -> Dict[str, Any]:
        """Create a new group chat thread with all enabled agents."""
        enabled_agents = list(user_context.enabled_agents or [])
        
        thread_data = {
            'title': 'Team Chat',
            'metadata': {
                'slug': 'group:all',
                'source': 'mobile',
                'agent_keys': enabled_agents
            }
        }
        
        return self.db.create_chat_thread(
            user_id=user_context.user_id,
            organization_id=user_context.organization_id,
            **thread_data
        )
    
    def _fix_thread_agent_keys(self, thread_id: str, agent_keys: List[str]) -> bool:
        """Fix agent_keys for a thread."""
        try:
            thread = self.db.get_chat_thread(thread_id)
        except TransientDatabaseError as exc:
            print(f"MobileChatService: transient error fetching thread {thread_id}: {exc}")
            return False
        if not thread:
            return False
        
        updated_metadata = {**thread.get('metadata', {}), 'agent_keys': agent_keys}
        
        result = self.db.update_chat_thread(thread_id, {'metadata': updated_metadata})
        return bool(result)


# Global instance for mobile chat service
mobile_chat_service = MobileChatService()
