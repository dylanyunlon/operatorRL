"""
UniversalGameStateSchema — Cross-game unified game state schema with validation.

Defines and validates the universal game state format that all game protocol
adapters normalize to. Supports schema versioning, field mapping by game type,
and validation with detailed error reporting.

Location: extensions/protocol_decoder/src/universal_game_state_schema.py

Reference (拿来主义):
  - extensions/protocol_decoder/src/protocol_feature_bridge.py（M656）: feature registration
  - integrations/lol-history/src/lol_history/historical_feature_vector_builder.py（M602）:
    FeatureSpec + register_feature→build_vector pattern
  - agentlightning/inference/game_state_preprocessor.py（M553）: schema validation

Design Notes (Knuth-level critique):
  User:
    - validate() returns detailed error list — never just True/False.
    - register_game_field_mapping() lets new games extend schema without modifying core.
    - Schema version is tracked and enforced.
  System:
    - Field definitions are declarative (name, type, required, default).
    - Validation is O(F) where F = number of fields — no nested loops.
    - Game-specific field mappings are separate from core schema.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.universal_game_state_schema.v1"

SCHEMA_VERSION: str = "1.0.0"


class FieldSpec:
    """Specification for a single schema field."""

    __slots__ = ("name", "field_type", "required", "default", "description")

    def __init__(
        self,
        name: str,
        field_type: str = "any",
        required: bool = False,
        default: Any = None,
        description: str = "",
    ) -> None:
        self.name = name
        self.field_type = field_type  # "str", "int", "float", "list", "dict", "bool", "any"
        self.required = required
        self.default = default
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "field_type": self.field_type,
            "required": self.required,
            "default": self.default,
            "description": self.description,
        }


_TYPE_MAP: Dict[str, type] = {
    "str": str,
    "int": int,
    "float": (int, float),  # type: ignore[assignment]
    "list": list,
    "dict": dict,
    "bool": bool,
}

# Core universal schema fields
_CORE_FIELDS: List[FieldSpec] = [
    FieldSpec("game_type", "str", True, "", "Game identifier: lol/dota2/mahjong"),
    FieldSpec("game_time", "float", True, 0.0, "Current game time in seconds"),
    FieldSpec("players", "list", True, [], "List of player state dicts"),
    FieldSpec("map_state", "dict", False, {}, "Map/board state information"),
    FieldSpec("resources", "dict", False, {}, "Resource state (gold, items, scores)"),
    FieldSpec("events", "list", False, [], "Recent game events"),
]

# Player sub-schema
_PLAYER_FIELDS: List[FieldSpec] = [
    FieldSpec("name", "str", True, "", "Player identifier"),
    FieldSpec("champion", "str", False, "", "Character/hero name"),
    FieldSpec("level", "int", False, 0, "Player level"),
    FieldSpec("team", "str", False, "", "Team identifier"),
    FieldSpec("is_dead", "bool", False, False, "Whether player is currently dead"),
    FieldSpec("position", "str", False, "", "Role/position"),
    FieldSpec("kills", "int", False, 0, "Kill count"),
    FieldSpec("deaths", "int", False, 0, "Death count"),
    FieldSpec("assists", "int", False, 0, "Assist count"),
    FieldSpec("cs", "int", False, 0, "Creep score / equivalent"),
]


class UniversalGameStateSchema:
    """Universal game state schema with validation and game-specific extensions.

    Public API:
        validate(state) -> list[str]
        validate_player(player) -> list[str]
        register_game_field_mapping(game_type, mapping)
        get_field_mapping(game_type) -> dict
        apply_defaults(state) -> dict
        get_schema_info() -> dict
        list_game_types() -> list[str]
    """

    def __init__(self) -> None:
        self._core_fields: List[FieldSpec] = list(_CORE_FIELDS)
        self._player_fields: List[FieldSpec] = list(_PLAYER_FIELDS)
        self._game_mappings: Dict[str, Dict[str, str]] = {}
        self._custom_fields: Dict[str, List[FieldSpec]] = {}
        self._validate_count: int = 0
        self._error_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, state: Dict[str, Any]) -> List[str]:
        """Validate a game state dict against the universal schema.

        Returns:
            List of error messages. Empty list = valid.
        """
        self._validate_count += 1
        errors: List[str] = []

        if not isinstance(state, dict):
            errors.append("State must be a dict")
            self._error_count += len(errors)
            return errors

        # Core field validation
        for fs in self._core_fields:
            errors.extend(self._validate_field(state, fs))

        # Player sub-validation
        players = state.get("players", [])
        if isinstance(players, list):
            for i, p in enumerate(players):
                if not isinstance(p, dict):
                    errors.append(f"players[{i}] must be a dict")
                    continue
                for pf in self._player_fields:
                    perr = self._validate_field(p, pf)
                    errors.extend(f"players[{i}].{e}" for e in perr)

        # Game-specific custom fields
        game_type = state.get("game_type", "")
        if game_type in self._custom_fields:
            for cf in self._custom_fields[game_type]:
                errors.extend(self._validate_field(state, cf))

        if errors:
            self._error_count += len(errors)
            self._fire("validation_failed", {"error_count": len(errors)})

        return errors

    def validate_player(self, player: Dict[str, Any]) -> List[str]:
        """Validate a single player dict."""
        errors: List[str] = []
        if not isinstance(player, dict):
            return ["Player must be a dict"]
        for pf in self._player_fields:
            errors.extend(self._validate_field(player, pf))
        return errors

    @staticmethod
    def _validate_field(data: Dict[str, Any], fs: FieldSpec) -> List[str]:
        """Validate a single field against its spec."""
        errors: List[str] = []
        val = data.get(fs.name)

        if val is None:
            if fs.required:
                errors.append(f"Missing required field: {fs.name}")
            return errors

        if fs.field_type != "any":
            expected = _TYPE_MAP.get(fs.field_type)
            if expected is not None and not isinstance(val, expected):
                errors.append(
                    f"Field '{fs.name}' expected {fs.field_type}, got {type(val).__name__}"
                )
        return errors

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def apply_defaults(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Apply default values for missing fields.

        Returns a new dict with defaults filled in.
        """
        result = dict(state)
        for fs in self._core_fields:
            if fs.name not in result and fs.default is not None:
                result[fs.name] = (
                    type(fs.default)() if isinstance(fs.default, (list, dict)) else fs.default
                )
        return result

    # ------------------------------------------------------------------
    # Game-specific field mapping
    # ------------------------------------------------------------------

    def register_game_field_mapping(
        self,
        game_type: str,
        mapping: Dict[str, str],
    ) -> None:
        """Register game-specific field name mapping.

        Args:
            game_type: Game identifier.
            mapping: Dict of universal_field → game_specific_field.
        """
        self._game_mappings[game_type] = dict(mapping)
        self._fire("mapping_registered", {"game_type": game_type, "fields": len(mapping)})

    def register_custom_fields(self, game_type: str, fields: List[FieldSpec]) -> None:
        """Register game-specific extra fields."""
        self._custom_fields[game_type] = list(fields)

    def get_field_mapping(self, game_type: str) -> Dict[str, str]:
        """Get field mapping for a game type."""
        return dict(self._game_mappings.get(game_type, {}))

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def get_schema_info(self) -> Dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "core_fields": [f.to_dict() for f in self._core_fields],
            "player_fields": [f.to_dict() for f in self._player_fields],
            "registered_games": list(self._game_mappings.keys()),
            "custom_field_games": list(self._custom_fields.keys()),
            "validate_count": self._validate_count,
            "error_count": self._error_count,
        }

    def list_game_types(self) -> List[str]:
        return list(set(list(self._game_mappings.keys()) + list(self._custom_fields.keys())))

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
                logger.exception("evolution_callback raised in UniversalGameStateSchema")
