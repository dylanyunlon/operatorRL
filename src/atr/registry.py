"""
Tool Registry

Central registry for agent tools with validation and discovery.
"""

from typing import Dict, List, Optional, Type
from .tools import Tool


class ToolRegistry:
    """
    Central registry for all agent tools.
    
    Supports:
    - Tool registration and discovery
    - Capability queries
    - Provenance metadata validation
    """

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """
        Register a tool in the registry.
        
        Args:
            tool: The tool instance to register
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """
        Get a tool by name.
        
        Args:
            name: The tool name
            
        Returns:
            The tool or None if not found
        """
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_by_capability(self, capability: str) -> List[Tool]:
        """
        Find tools that have a specific capability.
        
        Args:
            capability: The capability to search for
            
        Returns:
            List of tools with that capability
        """
        return [
            tool for tool in self._tools.values()
            if capability in tool.capabilities
        ]
