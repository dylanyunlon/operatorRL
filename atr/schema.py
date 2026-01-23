"""
Schema definitions for Agent Tool Registry.

Defines the rigorous JSON/Pydantic schema for tool specifications,
similar to OpenAI Function Calling spec.
"""

from typing import Any, Dict, List, Optional, Literal
from enum import Enum
from pydantic import BaseModel, Field, field_validator, PrivateAttr, ConfigDict


class ParameterType(str, Enum):
    """Supported parameter types for tool inputs/outputs."""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ParameterSpec(BaseModel):
    """Specification for a single parameter."""
    name: str = Field(..., description="Parameter name")
    type: ParameterType = Field(..., description="Parameter type")
    description: str = Field(..., description="Human-readable description")
    required: bool = Field(default=True, description="Whether parameter is required")
    default: Optional[Any] = Field(default=None, description="Default value if not required")
    enum: Optional[List[Any]] = Field(default=None, description="Allowed values (for enum types)")
    items: Optional[Dict[str, Any]] = Field(default=None, description="Array item schema (for array type)")
    properties: Optional[Dict[str, Any]] = Field(default=None, description="Object properties (for object type)")

    @field_validator("default")
    @classmethod
    def validate_default(cls, v, info):
        """Ensure default is only set for non-required parameters."""
        if v is not None and info.data.get("required", True):
            raise ValueError("Cannot set default value for required parameter")
        return v


class SideEffect(str, Enum):
    """Types of side effects a tool may have."""
    NONE = "none"
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    NETWORK = "network"
    FILESYSTEM = "filesystem"


class CostLevel(str, Enum):
    """Cost level for tool execution."""
    FREE = "free"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolMetadata(BaseModel):
    """Metadata about a tool."""
    name: str = Field(..., description="Unique tool identifier")
    description: str = Field(..., description="Human-readable tool description")
    version: str = Field(default="1.0.0", description="Tool version")
    author: Optional[str] = Field(default=None, description="Tool author")
    cost: CostLevel = Field(default=CostLevel.FREE, description="Estimated execution cost")
    side_effects: List[SideEffect] = Field(default_factory=lambda: [SideEffect.NONE], description="Tool side effects")
    tags: List[str] = Field(default_factory=list, description="Searchable tags")


class ToolSpec(BaseModel):
    """Complete specification for a tool.
    
    This is the core schema that defines what a tool looks like in the registry.
    It does NOT execute the tool - it just describes it.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    metadata: ToolMetadata = Field(..., description="Tool metadata")
    parameters: List[ParameterSpec] = Field(default_factory=list, description="Input parameters")
    returns: Optional[ParameterSpec] = Field(default=None, description="Return value specification")
    
    # The actual callable - stored but never executed by the registry
    # Use PrivateAttr for internal state that shouldn't be validated
    _callable_func: Optional[Any] = PrivateAttr(default=None)

    def to_openai_function_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format.
        
        Returns:
            Dictionary in OpenAI function calling format
        """
        properties = {}
        required = []
        
        for param in self.parameters:
            prop_schema = {
                "type": param.type.value,
                "description": param.description,
            }
            
            if param.enum:
                prop_schema["enum"] = param.enum
            if param.items:
                prop_schema["items"] = param.items
            if param.properties:
                prop_schema["properties"] = param.properties
                
            properties[param.name] = prop_schema
            
            if param.required:
                required.append(param.name)
        
        return {
            "name": self.metadata.name,
            "description": self.metadata.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            }
        }
