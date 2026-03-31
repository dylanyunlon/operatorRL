"""
CrossGameActionSpaceUnifier — Map different games' action spaces to unified abstract actions.

Provides bidirectional mapping between game-specific actions and a universal
abstract action space, enabling cross-game training data compatibility.

Location: extensions/protocol_decoder/src/cross_game_action_space_unifier.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/action_space_mapper.py: LoL action space mapping
  - integrations/lol-history/src/lol_history/historical_action_space_profiler.py（M620）:
    action distribution profiling
  - DI-star: StarCraft action space abstraction

Design Notes (Knuth-level critique):
  User:
    - encode() game-specific→abstract is O(1) dict lookup.
    - decode() abstract→game-specific is O(1) reverse lookup.
    - Unknown actions get a fallback 'unknown' code — never crashes.
  System:
    - Bidirectional mapping maintained via parallel dicts.
    - Per-game action frequency tracked for profiling.
    - Abstract action categories are extensible via register_category().
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.cross_game_action_space_unifier.v1"

# Universal abstract action categories
_ABSTRACT_CATEGORIES = {
    "move": "Movement/positioning",
    "attack": "Offensive action",
    "defend": "Defensive action",
    "use_ability": "Use skill/ability",
    "acquire_resource": "Buy/pick up resource",
    "communicate": "Signal/chat",
    "observe": "Scout/ward/vision",
    "wait": "Pass/do nothing",
    "strategic": "Macro strategy decision",
    "unknown": "Unmapped action",
}


class CrossGameActionSpaceUnifier:
    """Cross-game action space unifier.

    Public API:
        register_game_actions(game_type, action_map)
        encode(game_type, game_action) -> str
        decode(game_type, abstract_action) -> str
        batch_encode(game_type, actions) -> list[str]
        batch_decode(game_type, actions) -> list[str]
        get_abstract_categories() -> dict
        register_category(name, description)
        get_action_stats(game_type) -> dict
        get_coverage(game_type) -> dict
    """

    def __init__(self) -> None:
        # game_type → {game_action: abstract_action}
        self._forward: Dict[str, Dict[str, str]] = {}
        # game_type → {abstract_action: game_action}
        self._reverse: Dict[str, Dict[str, str]] = {}
        # Per-game action frequency
        self._encode_stats: Dict[str, Dict[str, int]] = {}
        self._categories: Dict[str, str] = dict(_ABSTRACT_CATEGORIES)
        self._op_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_game_actions(
        self,
        game_type: str,
        action_map: Dict[str, str],
    ) -> None:
        """Register action mapping for a game.

        Args:
            game_type: Game identifier.
            action_map: Dict of game_specific_action → abstract_action.
        """
        self._forward[game_type] = dict(action_map)
        self._reverse[game_type] = {v: k for k, v in action_map.items()}
        self._encode_stats[game_type] = {}
        self._fire("actions_registered", {
            "game_type": game_type,
            "action_count": len(action_map),
        })

    def register_category(self, name: str, description: str) -> None:
        """Register a new abstract action category."""
        self._categories[name] = description

    # ------------------------------------------------------------------
    # Encode / Decode
    # ------------------------------------------------------------------

    def encode(self, game_type: str, game_action: str) -> str:
        """Encode game-specific action to abstract action.

        Returns 'unknown' if no mapping exists.
        """
        self._op_count += 1
        mapping = self._forward.get(game_type, {})
        abstract = mapping.get(game_action, "unknown")

        # Track stats
        if game_type not in self._encode_stats:
            self._encode_stats[game_type] = {}
        self._encode_stats[game_type][game_action] = (
            self._encode_stats[game_type].get(game_action, 0) + 1
        )

        return abstract

    def decode(self, game_type: str, abstract_action: str) -> str:
        """Decode abstract action to game-specific action.

        Returns 'unknown' if no reverse mapping exists.
        """
        self._op_count += 1
        mapping = self._reverse.get(game_type, {})
        return mapping.get(abstract_action, "unknown")

    def batch_encode(self, game_type: str, actions: List[str]) -> List[str]:
        """Batch encode game actions to abstract actions."""
        return [self.encode(game_type, a) for a in actions]

    def batch_decode(self, game_type: str, actions: List[str]) -> List[str]:
        """Batch decode abstract actions to game actions."""
        return [self.decode(game_type, a) for a in actions]

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def get_abstract_categories(self) -> Dict[str, str]:
        return dict(self._categories)

    def get_action_stats(self, game_type: str) -> Dict[str, int]:
        return dict(self._encode_stats.get(game_type, {}))

    def get_coverage(self, game_type: str) -> Dict[str, Any]:
        """Get mapping coverage statistics for a game."""
        fwd = self._forward.get(game_type, {})
        abstract_used: Set[str] = set(fwd.values())
        return {
            "game_type": game_type,
            "mapped_actions": len(fwd),
            "abstract_categories_used": len(abstract_used),
            "abstract_categories_total": len(self._categories),
            "coverage_ratio": len(abstract_used) / max(len(self._categories), 1),
            "unmapped_categories": [
                c for c in self._categories if c not in abstract_used
            ],
        }

    def list_registered_games(self) -> List[str]:
        return list(self._forward.keys())

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "registered_games": self.list_registered_games(),
            "categories": len(self._categories),
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")
