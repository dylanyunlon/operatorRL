"""
Episode Schema - The core data structure for episodic memory.

This module defines the immutable Episode data structure that represents
a single agent experience: Goal -> Action -> Result -> Reflection.
"""

from datetime import datetime, timezone
from typing import Any, Dict
from pydantic import BaseModel, Field, model_validator
import hashlib
import json


class Episode(BaseModel):
    """
    An immutable episode representing a single agent experience.
    
    Episodes follow the pattern: Goal -> Action -> Result -> Reflection
    and are stored in an append-only manner with no modifications allowed.
    
    Attributes:
        goal: The agent's intended objective
        action: The action taken to achieve the goal
        result: The outcome of the action
        reflection: Agent's analysis or learning from the experience
        timestamp: When the episode was created (auto-generated)
        metadata: Additional context or tags for indexing
        episode_id: Unique hash-based identifier (auto-generated)
    """
    
    goal: str = Field(..., description="The agent's intended objective")
    action: str = Field(..., description="The action taken to achieve the goal")
    result: str = Field(..., description="The outcome of the action")
    reflection: str = Field(..., description="Agent's analysis or learning from the experience")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), 
        description="When the episode was created"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context or tags")
    episode_id: str = Field(default="", description="Unique hash-based identifier")
    
    model_config = {
        "frozen": True,  # Truly immutable
        "json_schema_extra": {
            "example": {
                "goal": "Retrieve user preferences",
                "action": "Query database for user_id=123",
                "result": "Successfully retrieved preferences",
                "reflection": "Database query was efficient and returned expected data",
                "metadata": {"user_id": "123", "query_time_ms": 45}
            }
        }
    }
    
    @model_validator(mode='before')
    @classmethod
    def generate_episode_id(cls, data: Any) -> Any:
        """Generate episode_id if not provided."""
        if isinstance(data, dict):
            if not data.get('episode_id'):
                # Generate the hash
                content = {
                    "goal": data.get('goal', ''),
                    "action": data.get('action', ''),
                    "result": data.get('result', ''),
                    "reflection": data.get('reflection', ''),
                    "timestamp": data.get('timestamp', datetime.now(timezone.utc)).isoformat() 
                                if isinstance(data.get('timestamp'), datetime) 
                                else data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                }
                content_str = json.dumps(content, sort_keys=True)
                data['episode_id'] = hashlib.sha256(content_str.encode()).hexdigest()
        return data
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert episode to dictionary format."""
        return self.model_dump()
    
    def to_json(self) -> str:
        """Convert episode to JSON string."""
        return self.model_dump_json()
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Episode":
        """Create episode from dictionary."""
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> "Episode":
        """Create episode from JSON string."""
        return cls.model_validate_json(json_str)
