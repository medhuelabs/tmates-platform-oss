"""
Thread Management - Ensures agent_keys are correctly configured for mobile app

This provides basic thread validation for edge cases, since ChatManager's
automatic thread manipulation has been deprecated in favor of mobile-first architecture.
"""

from typing import List, Dict, Any, Optional
from app.db import get_database_client
from app.auth import UserContext


class ThreadManager:
    """
    Basic thread validation for mobile-first architecture.
    
    Note: This is now primarily for edge case handling since ChatManager's
    automatic thread manipulation has been deprecated. The mobile app manages
    its own thread configurations through MobileChatService.
    """
    
    def __init__(self):
        self.db = get_database_client()
    
    def ensure_agent_keys(self, thread_id: str, user_context: UserContext) -> bool:
        """
        Ensures a thread has correct agent_keys based on its title and user's enabled agents.
        Returns True if the thread was updated, False if it was already correct.
        """
        try:
            thread = self.db.get_chat_thread(thread_id)
            if not thread:
                return False
            
            title = thread.get('title', '').lower()
            current_agent_keys = thread.get('agent_keys', [])  # agent_keys are on the thread directly
            enabled_agents = user_context.enabled_agents or []
            
            # Determine what agent_keys should be based on thread title
            expected_agent_keys = self._get_expected_agent_keys(title, enabled_agents)
            
            # If current agent_keys don't match expected, fix them
            if sorted(current_agent_keys) != sorted(expected_agent_keys):
                print(f"ThreadManager: Fixing thread {thread_id} - '{thread.get('title')}'")
                print(f"  Current agent_keys: {current_agent_keys}")
                print(f"  Expected agent_keys: {expected_agent_keys}")
                
                # Update the agent_keys directly on the thread
                result = self.db.update_chat_thread(thread_id, {
                    'agent_keys': expected_agent_keys
                })
                
                if result:
                    print(f"  ✅ Fixed agent_keys: {result.get('agent_keys', [])}")
                    return True
                else:
                    print(f"  ❌ Failed to fix agent_keys")
                    return False
            
            return False  # No update needed
            
        except Exception as e:
            print(f"ThreadManager: Error ensuring agent_keys for thread {thread_id}: {e}")
            return False
    
    def ensure_all_user_threads(self, user_id: str, user_context: UserContext, organization_id: str) -> int:
        """
        Ensures all of a user's threads have correct agent_keys.
        Returns the number of threads that were fixed.
        """
        try:
            threads = self.db.list_chat_threads(user_id, organization_id=organization_id, limit=50)
            fixed_count = 0
            
            for thread in threads:
                thread_id = thread.get('id')
                if thread_id and self.ensure_agent_keys(thread_id, user_context):
                    fixed_count += 1
            
            if fixed_count > 0:
                print(f"ThreadManager: Fixed {fixed_count} threads for user {user_id}")
            
            return fixed_count
            
        except Exception as e:
            print(f"ThreadManager: Error ensuring all threads for user {user_id}: {e}")
            return 0
    
    def _get_expected_agent_keys(self, title: str, enabled_agents: List[str]) -> List[str]:
        """
        Determines what agent_keys should be based on thread title and enabled agents.
        Uses dynamic agent discovery instead of hard-coded agent names.
        """
        title_lower = title.lower()
        
        # Group chat should have all enabled agents
        if 'group' in title_lower:
            return list(enabled_agents)
        
        # Individual agent chats should have only that specific agent if it's enabled
        for agent in enabled_agents:
            if agent.lower() in title_lower:
                return [agent.lower()]
        
        # If title matches any known agent but it's not enabled, return empty
        # This prevents threads for disabled agents from being processed
        from app.registry.agents.repository import AgentRepository
        agent_repo = AgentRepository()
        all_agent_keys = list(agent_repo.keys())
        
        for agent in all_agent_keys:
            if agent.lower() in title_lower:
                return []  # Agent exists but not enabled
        
        # Default to all enabled agents for unrecognized thread types
        return list(enabled_agents)


# Global instance for easy access
thread_manager = ThreadManager()