"""
ATR - Agent Tool Registry

A decentralized marketplace for agent capabilities.
Provides a standardized interface for tool discovery and registration.
"""

from .schema import ToolSpec, ParameterSpec, ToolMetadata
from .registry import Registry
from .decorator import register as register_decorator

__version__ = "0.1.0"
__all__ = [
    "ToolSpec",
    "ParameterSpec", 
    "ToolMetadata",
    "Registry",
    "register",
]

# Global registry instance
_global_registry = Registry()

# Create a register instance that uses the global registry by default
def register(
    name=None,
    description=None,
    version="1.0.0",
    author=None,
    cost="free",
    side_effects=None,
    tags=None,
    registry=None,
):
    """Decorator to register a function as a tool in the global ATR.
    
    Usage:
        @atr.register(name="my_tool", cost="low")
        def my_function(x: int) -> int:
            return x * 2
    """
    if registry is None:
        registry = _global_registry
    
    return register_decorator(
        name=name,
        description=description,
        version=version,
        author=author,
        cost=cost,
        side_effects=side_effects,
        tags=tags,
        registry=registry,
    )

