"""
Tool Registry System

Machine-readable registry of agent capabilities for intelligent task routing.
The registry analyses task content and matches it against tools defined in
each agent's `manifest.yaml`.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from functools import lru_cache
import re
from dataclasses import dataclass

from ...config import INSTALLED_AGENTS
# Import log locally to avoid circular imports

def _log(*args):
    """Local log wrapper to avoid circular imports."""
    try:
        from logs import log
        log(*args)
    except ImportError:
        print(*args)  # Fallback to print if logging not available

# Define PROJECT_ROOT here since it's not in config.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class ToolDefinition:
    """Represents a single tool capability."""
    name: str
    description: str
    agent_key: str
    categories: List[str]
    keywords: List[str]
    content_patterns: List[str]
    task_types: List[str]
    input_requirements: Dict[str, List[str]]
    output_format: Dict[str, Any]
    confidence_weights: Dict[str, float]

    def matches_content(self, content: str) -> float:
        """Calculate match confidence for given content."""
        if not content:
            return 0.0
        
        content_lower = content.lower()
        score = 0.0
        
        # Keyword matching
        keyword_matches = sum(1 for kw in self.keywords if kw.lower() in content_lower)
        if keyword_matches > 0:
            score += (keyword_matches / len(self.keywords)) * self.confidence_weights.get('keywords', 0.3)
        
        # Pattern matching (regex)
        pattern_matches = 0
        for pattern in self.content_patterns:
            try:
                if re.search(pattern, content, re.IGNORECASE):
                    pattern_matches += 1
            except re.error:
                # Treat as literal string if regex fails
                if pattern.lower() in content_lower:
                    pattern_matches += 1
        
        if pattern_matches > 0:
            score += (pattern_matches / len(self.content_patterns)) * self.confidence_weights.get('patterns', 0.4)
        
        return min(score, 1.0)  # Cap at 1.0


@dataclass
class AgentRegistry:
    """Registry of all agent tools and capabilities."""
    agents: Dict[str, List[ToolDefinition]]
    
    def get_all_tools(self) -> List[ToolDefinition]:
        """Get flat list of all tools across agents."""
        tools = []
        for agent_tools in self.agents.values():
            tools.extend(agent_tools)
        return tools
    
    def find_best_agent(self, task_content: str, task_title: str = "") -> Tuple[Optional[str], float]:
        """Find best agent for task content. Returns (agent_key, confidence)."""
        if not task_content and not task_title:
            return None, 0.0
        
        combined_content = f"{task_title} {task_content}".strip()
        best_agent = None
        best_score = 0.0
        
        for agent_key, tools in self.agents.items():
            agent_max_score = 0.0
            for tool in tools:
                score = tool.matches_content(combined_content)
                agent_max_score = max(agent_max_score, score)
            
            if agent_max_score > best_score:
                best_score = agent_max_score
                best_agent = agent_key
        
        return best_agent, best_score
    
    def get_agent_capabilities(self, agent_key: str) -> List[ToolDefinition]:
        """Get all tools for specific agent."""
        return self.agents.get(agent_key, [])
    
    def search_tools(self, query: str, category: Optional[str] = None) -> List[ToolDefinition]:
        """Search tools by query and optional category."""
        results = []
        query_lower = query.lower()
        
        for tool in self.get_all_tools():
            # Category filter
            if category and category.lower() not in [cat.lower() for cat in tool.categories]:
                continue
            
            # Text search in name, description, keywords
            if (query_lower in tool.name.lower() or 
                query_lower in tool.description.lower() or
                any(query_lower in kw.lower() for kw in tool.keywords)):
                results.append(tool)
        
        return results


def _parse_tool_from_manifest(agent_key: str, tool_data: Dict[str, Any]) -> ToolDefinition:
    """Parse tool definition from manifest YAML data."""
    
    # Default confidence weights if not specified
    default_weights = {
        'keywords': 0.3,
        'patterns': 0.4,
        'task_types': 0.3
    }
    
    return ToolDefinition(
        name=tool_data.get('name', 'unnamed_tool'),
        description=tool_data.get('description', ''),
        agent_key=agent_key,
        categories=tool_data.get('categories', []),
        keywords=tool_data.get('task_matching', {}).get('keywords', []),
        content_patterns=tool_data.get('task_matching', {}).get('content_patterns', []),
        task_types=tool_data.get('task_matching', {}).get('task_types', []),
        input_requirements=tool_data.get('input_requirements', {'required': [], 'optional': []}),
        output_format=tool_data.get('output_format', {}),
        confidence_weights=tool_data.get('confidence_weights', default_weights)
    )


def _load_agent_manifest(agent_key: str) -> Optional[Dict[str, Any]]:
    """Load agent manifest.yaml file."""
    manifest_path = Path(PROJECT_ROOT) / "app" / "agents" / agent_key / "manifest.yaml"
    
    if not manifest_path.exists():
        _log(f"[tools_registry] manifest not found for agent: {agent_key}")
        return None
    
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as exc:
        _log(f"[tools_registry] failed to load manifest for {agent_key}: {exc}")
        return None


@lru_cache(maxsize=1)
def load_tools_registry() -> AgentRegistry:
    """Load and cache the complete tools registry from all agent manifests."""
    registry = AgentRegistry(agents={})
    
    for agent_key in INSTALLED_AGENTS:
        if agent_key == "test":  # Skip test agent
            continue
            
        manifest = _load_agent_manifest(agent_key)
        if not manifest:
            continue
        
        tools_data = manifest.get('tools', [])
        if not tools_data:
            _log(f"[tools_registry] no tools defined for agent: {agent_key}")
            continue
        
        agent_tools = []
        for tool_data in tools_data:
            try:
                tool = _parse_tool_from_manifest(agent_key, tool_data)
                agent_tools.append(tool)
            except Exception as exc:
                _log(f"[tools_registry] failed to parse tool for {agent_key}: {exc}")
        
        if agent_tools:
            registry.agents[agent_key] = agent_tools
            _log(f"[tools_registry] loaded {len(agent_tools)} tools for {agent_key}")
    
    _log(f"[tools_registry] loaded registry with {len(registry.agents)} agents")
    return registry


def get_tools_registry() -> AgentRegistry:
    """Get the cached tools registry."""
    return load_tools_registry()


def find_agent_for_task(task_content: str, task_title: str = "", min_confidence: float = 0.1) -> Optional[str]:
    """Find best agent for task. Returns None if confidence too low."""
    registry = get_tools_registry()
    agent_key, confidence = registry.find_best_agent(task_content, task_title)
    
    if confidence >= min_confidence:
        return agent_key
    return None


def get_available_tools() -> Dict[str, List[Dict[str, Any]]]:
    """Get all available tools in API-friendly format."""
    registry = get_tools_registry()
    result = {}
    
    for agent_key, tools in registry.agents.items():
        result[agent_key] = []
        for tool in tools:
            result[agent_key].append({
                'name': tool.name,
                'description': tool.description,
                'categories': tool.categories,
                'keywords': tool.keywords,
                'input_requirements': tool.input_requirements,
                'output_format': tool.output_format
            })
    
    return result


def refresh_registry():
    """Clear cache and reload registry (for development/testing)."""
    load_tools_registry.cache_clear()
    return load_tools_registry()


# Development/debugging functions
def debug_task_matching(task_content: str, task_title: str = "") -> Dict[str, Any]:
    """Debug tool matching for a task. Returns detailed scoring information."""
    registry = get_tools_registry()
    combined_content = f"{task_title} {task_content}".strip()
    
    results = {}
    for agent_key, tools in registry.agents.items():
        agent_scores = []
        for tool in tools:
            score = tool.matches_content(combined_content)
            agent_scores.append({
                'tool_name': tool.name,
                'score': score,
                'keywords_matched': [kw for kw in tool.keywords if kw.lower() in combined_content.lower()],
                'patterns_matched': []  # Could implement pattern details
            })
        results[agent_key] = {
            'tools': agent_scores,
            'max_score': max((t['score'] for t in agent_scores), default=0.0)
        }
    
    best_agent, best_score = registry.find_best_agent(task_content, task_title)
    results['recommendation'] = {
        'agent': best_agent,
        'confidence': best_score
    }
    
    return results


if __name__ == "__main__":
    # Test the registry
    registry = load_tools_registry()
    print(f"Loaded {len(registry.agents)} agents:")
    for agent_key, tools in registry.agents.items():
        print(f"  {agent_key}: {len(tools)} tools")
        for tool in tools:
            print(f"    - {tool.name}: {tool.description}")
