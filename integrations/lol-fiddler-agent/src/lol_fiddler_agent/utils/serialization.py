"""
Serialization Helpers - JSON/MessagePack serialization utilities.

Provides fast serialization for game state data with custom
encoders for datetime, enum, and numpy types.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GameDataEncoder(json.JSONEncoder):
    """Custom JSON encoder for game data types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "__dataclass_fields__"):
            from dataclasses import asdict
            return asdict(obj)
        try:
            import numpy as np
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return super().default(obj)


def to_json(data: Any, pretty: bool = False) -> str:
    """Serialize data to JSON with custom encoder."""
    indent = 2 if pretty else None
    return json.dumps(data, cls=GameDataEncoder, indent=indent, default=str)


def from_json(data: str) -> Any:
    """Deserialize JSON string."""
    return json.loads(data)


def safe_serialize(data: Any, max_depth: int = 10) -> Any:
    """Safely serialize data, handling circular references."""
    seen: set[int] = set()

    def _serialize(obj: Any, depth: int) -> Any:
        if depth > max_depth:
            return "<max_depth>"

        obj_id = id(obj)
        if obj_id in seen:
            return "<circular_ref>"

        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value

        seen.add(obj_id)
        try:
            if isinstance(obj, dict):
                return {str(k): _serialize(v, depth + 1) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_serialize(item, depth + 1) for item in obj]
            if hasattr(obj, "model_dump"):
                return _serialize(obj.model_dump(), depth + 1)
            if hasattr(obj, "__dict__"):
                return _serialize(obj.__dict__, depth + 1)
            return str(obj)
        finally:
            seen.discard(obj_id)

    return _serialize(data, 0)


def compress_game_state(state: dict[str, Any]) -> dict[str, Any]:
    """Compress game state by removing redundant fields."""
    compressed = dict(state)

    # Remove verbose descriptions from items
    if "allPlayers" in compressed:
        for player in compressed["allPlayers"]:
            if "items" in player:
                for item in player.get("items", []):
                    item.pop("rawDescription", None)
                    item.pop("rawDisplayName", None)

    # Remove verbose ability descriptions
    if "activePlayer" in compressed and compressed["activePlayer"]:
        ap = compressed["activePlayer"]
        if "abilities" in ap and ap["abilities"]:
            for key in ("Passive", "Q", "W", "E", "R"):
                ability = ap["abilities"].get(key, {})
                if isinstance(ability, dict):
                    ability.pop("rawDescription", None)

    return compressed
