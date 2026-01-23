"""
Registry for storing and retrieving tool specifications.

The registry is a lightweight lookup mechanism that stores tool specs
but does NOT execute them. Execution is the responsibility of the
Agent Runtime (Control Plane).
"""

from typing import Dict, List, Optional, Callable, Any
from .schema import ToolSpec, ToolMetadata, SideEffect, CostLevel


class RegistryError(Exception):
    """Base exception for registry errors."""
    pass


class ToolNotFoundError(RegistryError):
    """Raised when a tool is not found in the registry."""
    pass


class ToolAlreadyExistsError(RegistryError):
    """Raised when attempting to register a tool that already exists."""
    pass


class Registry:
    """Lightweight tool registry using a local dictionary store.
    
    This registry stores tool specifications and their callables but does NOT
    execute them. It's purely a lookup and discovery mechanism.
    
    The actual execution is handled by the Agent Runtime (Control Plane).
    """

    def __init__(self):
        """Initialize an empty registry."""
        self._tools: Dict[str, ToolSpec] = {}

    def register_tool(
        self,
        spec: ToolSpec,
        callable_func: Optional[Callable] = None,
        replace: bool = False
    ) -> None:
        """Register a tool in the registry.
        
        Args:
            spec: The tool specification
            callable_func: The actual callable function (stored but not executed)
            replace: Whether to replace if tool already exists
            
        Raises:
            ToolAlreadyExistsError: If tool exists and replace=False
        """
        tool_name = spec.metadata.name
        
        if tool_name in self._tools and not replace:
            raise ToolAlreadyExistsError(
                f"Tool '{tool_name}' already exists. Use replace=True to overwrite."
            )
        
        # Store the callable but NEVER execute it
        if callable_func is not None:
            spec._callable_func = callable_func
            
        self._tools[tool_name] = spec

    def get_tool(self, name: str) -> ToolSpec:
        """Retrieve a tool specification by name.
        
        Args:
            name: The tool name
            
        Returns:
            The tool specification (includes the callable but doesn't execute it)
            
        Raises:
            ToolNotFoundError: If tool is not found
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' not found in registry")
        
        return self._tools[name]

    def get_callable(self, name: str) -> Callable:
        """Get the callable function for a tool.
        
        This returns the function object but does NOT execute it.
        The caller (Agent Runtime) is responsible for execution.
        
        Args:
            name: The tool name
            
        Returns:
            The callable function object
            
        Raises:
            ToolNotFoundError: If tool is not found
            ValueError: If tool has no callable
        """
        tool = self.get_tool(name)
        
        if tool._callable_func is None:
            raise ValueError(f"Tool '{name}' has no callable function")
        
        return tool._callable_func

    def list_tools(
        self,
        tag: Optional[str] = None,
        cost: Optional[CostLevel] = None,
        side_effect: Optional[SideEffect] = None
    ) -> List[ToolSpec]:
        """List all registered tools with optional filtering.
        
        Args:
            tag: Filter by tag
            cost: Filter by cost level
            side_effect: Filter by side effect
            
        Returns:
            List of matching tool specifications
        """
        tools = list(self._tools.values())
        
        if tag is not None:
            tools = [t for t in tools if tag in t.metadata.tags]
            
        if cost is not None:
            tools = [t for t in tools if t.metadata.cost == cost]
            
        if side_effect is not None:
            tools = [t for t in tools if side_effect in t.metadata.side_effects]
        
        return tools

    def search_tools(self, query: str) -> List[ToolSpec]:
        """Search tools by name, description, or tags.
        
        Args:
            query: Search query string
            
        Returns:
            List of matching tool specifications
        """
        query_lower = query.lower()
        results = []
        
        for tool in self._tools.values():
            # Check name
            if query_lower in tool.metadata.name.lower():
                results.append(tool)
                continue
                
            # Check description
            if query_lower in tool.metadata.description.lower():
                results.append(tool)
                continue
                
            # Check tags
            if any(query_lower in tag.lower() for tag in tool.metadata.tags):
                results.append(tool)
                continue
        
        return results

    def unregister_tool(self, name: str) -> None:
        """Remove a tool from the registry.
        
        Args:
            name: The tool name
            
        Raises:
            ToolNotFoundError: If tool is not found
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' not found in registry")
        
        del self._tools[name]

    def clear(self) -> None:
        """Remove all tools from the registry."""
        self._tools.clear()

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
