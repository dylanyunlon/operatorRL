"""
DataQualityValidator — LCU response validation and data scoring.
==================================================================
modules/canbus/vehicle/data_quality_validator.py

Claude17: Apollo validates CAN bus frames for correct DLC, CRC, and
timing. We validate LCU JSON responses for completeness, freshness,
and internal consistency before passing to perception.

Architecture position:
    modules/canbus/vehicle/data_quality_validator.py  ← YOU ARE HERE
    ├─ Called by: canbus_component.py after each LCU poll
    ├─ Validates: allgamedata JSON structure and values
    ├─ Scores: data quality 0.0–1.0 for downstream confidence
    └─ Publishes: validation warnings on /lol/canbus_warnings

Apollo reference:
    modules/canbus/common/canbus_gflags.h — validation flags
    modules/drivers/canbus/can_comm/message_manager.h — frame validation

Design notes:
    - Schema validation: required keys, types, ranges
    - Freshness: game time must advance between polls
    - Consistency: player count, team assignment, gold values
    - Scoring: weighted sum of check results
    - Zero external dependencies
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ValidationResult:
    """Result of validating one LCU allgamedata response."""
    valid: bool
    score: float  # 0.0–1.0
    checks_passed: int
    checks_total: int
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "score": round(self.score, 4),
            "passed": self.checks_passed,
            "total": self.checks_total,
            "warnings": self.warnings[:10],
            "errors": self.errors[:10],
        }


class DataQualityValidator:
    """Validates LCU allgamedata JSON before downstream consumption.

    Claude17: Multi-level validation:
        Level 1: Schema — required keys present
        Level 2: Types — values have expected types
        Level 3: Ranges — numeric values within reasonable bounds
        Level 4: Consistency — cross-field checks
        Level 5: Freshness — game time advancing

    Usage::

        validator = DataQualityValidator()
        result = validator.validate(allgamedata_dict)
        if result.score < 0.5:
            logger.warning("Low quality data: %s", result.warnings)
    """

    # Required top-level keys in allgamedata
    REQUIRED_KEYS = {"allPlayers", "gameData"}
    OPTIONAL_KEYS = {"activePlayer", "events"}

    # Expected types
    KEY_TYPES = {
        "allPlayers": list,
        "gameData": dict,
        "activePlayer": dict,
        "events": dict,
    }

    # Game time bounds
    MIN_GAME_TIME = 0.0
    MAX_GAME_TIME = 7200.0  # 2 hours max
    MAX_GOLD = 100000  # No player should have >100k gold
    EXPECTED_PLAYER_COUNT = 10

    def __init__(self) -> None:
        self._last_game_time: float = -1.0
        self._stale_count: int = 0
        self._validation_count: int = 0
        self._total_score: float = 0.0
        self._error_counts: Dict[str, int] = {}

    def validate(self, data: Any) -> ValidationResult:
        """Validate an allgamedata response.

        Args:
            data: Parsed JSON dict from LCU API.

        Returns:
            ValidationResult with score and details.
        """
        self._validation_count += 1
        checks_passed = 0
        checks_total = 0
        warnings: List[str] = []
        errors: List[str] = []

        # ── Level 1: Schema ──────────────────────────────────────────
        checks_total += 1
        if not isinstance(data, dict):
            errors.append("Response is not a dict")
            result = ValidationResult(
                valid=False, score=0.0,
                checks_passed=0, checks_total=1,
                errors=errors,
            )
            self._record_errors(errors)
            return result
        checks_passed += 1

        for key in self.REQUIRED_KEYS:
            checks_total += 1
            if key in data:
                checks_passed += 1
            else:
                errors.append(f"Missing required key: {key}")

        # ── Level 2: Types ───────────────────────────────────────────
        for key, expected_type in self.KEY_TYPES.items():
            if key in data:
                checks_total += 1
                if isinstance(data[key], expected_type):
                    checks_passed += 1
                else:
                    warnings.append(
                        f"{key} is {type(data[key]).__name__}, "
                        f"expected {expected_type.__name__}"
                    )

        # ── Level 3: Ranges ──────────────────────────────────────────
        game_data = data.get("gameData", {})
        if isinstance(game_data, dict):
            game_time = game_data.get("gameTime", -1)
            checks_total += 1
            if (isinstance(game_time, (int, float))
                    and self.MIN_GAME_TIME <= game_time <= self.MAX_GAME_TIME):
                checks_passed += 1
            else:
                warnings.append(
                    f"gameTime out of range: {game_time}"
                )

        all_players = data.get("allPlayers", [])
        if isinstance(all_players, list):
            # Player count check
            checks_total += 1
            if len(all_players) == self.EXPECTED_PLAYER_COUNT:
                checks_passed += 1
            elif len(all_players) > 0:
                checks_passed += 0.5  # Partial credit
                warnings.append(
                    f"Player count: {len(all_players)} "
                    f"(expected {self.EXPECTED_PLAYER_COUNT})"
                )
            else:
                errors.append("No players in allPlayers")

            # Gold range check
            for player in all_players:
                if isinstance(player, dict):
                    scores = player.get("scores", {})
                    if isinstance(scores, dict):
                        gold = scores.get("gold", 0)  # Fixed: use 'gold' key
                        checks_total += 1
                        if isinstance(gold, (int, float)) and 0 <= gold <= self.MAX_GOLD:
                            checks_passed += 1
                        else:
                            warnings.append(
                                f"Invalid gold value for "
                                f"{player.get('summonerName', '?')}: {gold}"
                            )

        # ── Level 4: Consistency ─────────────────────────────────────
        # Check team assignment consistency
        if isinstance(all_players, list) and len(all_players) > 0:
            teams = set()
            for p in all_players:
                if isinstance(p, dict):
                    teams.add(p.get("team", ""))
            checks_total += 1
            if len(teams) == 2:
                checks_passed += 1
            elif len(teams) > 0:
                warnings.append(
                    f"Unexpected team count: {len(teams)} ({teams})"
                )

        # ── Level 5: Freshness ───────────────────────────────────────
        if isinstance(game_data, dict):
            game_time = game_data.get("gameTime", -1)
            if isinstance(game_time, (int, float)) and game_time > 0:
                checks_total += 1
                if game_time > self._last_game_time:
                    checks_passed += 1
                    self._stale_count = 0
                else:
                    self._stale_count += 1
                    if self._stale_count > 5:
                        warnings.append(
                            f"Stale data: gameTime={game_time} "
                            f"(stale_count={self._stale_count})"
                        )
                self._last_game_time = game_time

        # ── Compute score ────────────────────────────────────────────
        score = checks_passed / max(checks_total, 1)
        valid = len(errors) == 0 and score >= 0.5

        self._total_score += score
        self._record_errors(errors + warnings)

        return ValidationResult(
            valid=valid,
            score=round(score, 4),
            checks_passed=int(checks_passed),
            checks_total=checks_total,
            warnings=warnings,
            errors=errors,
        )

    def _record_errors(self, messages: List[str]) -> None:
        """Track error frequency for diagnostics."""
        for msg in messages:
            # Use first 30 chars as key
            key = msg[:30]
            self._error_counts[key] = self._error_counts.get(key, 0) + 1

    @property
    def average_score(self) -> float:
        if self._validation_count == 0:
            return 0.0
        return round(self._total_score / self._validation_count, 4)

    def stats(self) -> Dict[str, Any]:
        return {
            "validation_count": self._validation_count,
            "average_score": self.average_score,
            "stale_count": self._stale_count,
            "top_errors": dict(
                sorted(
                    self._error_counts.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:10]
            ),
        }
