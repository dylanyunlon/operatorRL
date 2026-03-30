"""
Training Data Validator — sample integrity and completeness check.

Validates training samples before they enter the RL training pipeline.
Checks required fields, type correctness, value ranges, and custom rules.

Location: extensions/protocol_decoder/src/training_data_validator.py

Reference (拿来主义):
  - DI-star data validation: schema checks before training
  - agentlightning/types.py: Dataset validation patterns
  - extensions/fiddler_bridge/src/fiddler_training_pipeline.py: pipeline output
  - agentos/governance/policy_enforcer.py: rule-based enforcement

Design Notes (Knuth-level critique):
  User:
    - validate() always returns a result dict — never throws.
    - Custom rules via add_custom_rule() extend validation without subclassing.
    - batch_validate returns per-sample results — no silent aggregation.
  System:
    - Rule evaluation is O(R) per sample where R = number of rules.
    - Report is O(1) — stats are maintained incrementally.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.training_data_validator.v1"

_DEFAULT_REQUIRED_FIELDS: Tuple[str, ...] = ("game_time",)

_DEFAULT_TYPE_RULES: Dict[str, type] = {
    "game_time": (int, float),
    "gold": (int, float),
    "xp": (int, float),
    "level": (int, float),
}

_DEFAULT_RANGES: Dict[str, Tuple[float, float]] = {
    "game_time": (0.0, 7200.0),
    "gold": (0.0, 200_000.0),
    "level": (0.0, 30.0),
}


class _ValidationResult:
    """Single validation result."""

    __slots__ = ("valid", "errors")

    def __init__(self) -> None:
        self.valid = True
        self.errors: List[str] = []

    def fail(self, msg: str) -> None:
        self.valid = False
        self.errors.append(msg)

    def to_dict(self) -> Dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors}


class TrainingDataValidator:
    """Validate training samples for integrity and completeness.

    Attributes:
        validated_count: Total samples validated.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        *,
        required_fields: Sequence[str] | None = None,
        type_rules: Dict[str, type] | None = None,
        ranges: Dict[str, Tuple[float, float]] | None = None,
    ) -> None:
        self._required = list(required_fields) if required_fields else list(_DEFAULT_REQUIRED_FIELDS)
        self._type_rules: Dict[str, Any] = dict(type_rules) if type_rules else dict(_DEFAULT_TYPE_RULES)
        self._ranges: Dict[str, Tuple[float, float]] = dict(ranges) if ranges else dict(_DEFAULT_RANGES)
        self._custom_rules: Dict[str, Callable[[Dict[str, Any]], bool]] = {}

        self._validated_count: int = 0
        self._valid_count: int = 0
        self._invalid_count: int = 0

        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def validated_count(self) -> int:
        return self._validated_count

    # ------------------------------------------------------------------
    # Custom rules
    # ------------------------------------------------------------------

    def add_custom_rule(self, name: str, fn: Callable[[Dict[str, Any]], bool]) -> None:
        """Register a custom validation rule.

        Args:
            name: Human-readable rule name.
            fn: Callable that returns True if the sample passes.
        """
        self._custom_rules[name] = fn

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(self, sample: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a single training sample.

        Returns:
            Dict with ``valid`` bool and ``errors`` list.
        """
        self._validated_count += 1
        result = _ValidationResult()

        # 1. Required fields
        for field in self._required:
            if field not in sample:
                result.fail(f"missing required field: {field}")

        # 2. Type checks
        for field, expected_type in self._type_rules.items():
            if field in sample:
                val = sample[field]
                if not isinstance(val, expected_type):
                    result.fail(
                        f"type error: {field} expected {expected_type}, "
                        f"got {type(val).__name__}"
                    )

        # 3. Range checks
        for field, (lo, hi) in self._ranges.items():
            if field in sample:
                val = sample[field]
                if isinstance(val, (int, float)):
                    if val < lo or val > hi:
                        result.fail(
                            f"range error: {field}={val} not in [{lo}, {hi}]"
                        )

        # 4. Custom rules
        for rule_name, rule_fn in self._custom_rules.items():
            try:
                if not rule_fn(sample):
                    result.fail(f"custom rule failed: {rule_name}")
            except Exception as exc:
                result.fail(f"custom rule error: {rule_name}: {exc}")

        # Stats
        if result.valid:
            self._valid_count += 1
        else:
            self._invalid_count += 1

        self._fire_evolution({
            "action": "validate",
            "valid": result.valid,
            "error_count": len(result.errors),
        })

        return result.to_dict()

    def batch_validate(self, samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate a batch of samples."""
        return [self.validate(s) for s in samples]

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def get_report(self) -> Dict[str, Any]:
        return {
            "total": self._validated_count,
            "valid": self._valid_count,
            "invalid": self._invalid_count,
            "valid_rate": (
                self._valid_count / self._validated_count
                if self._validated_count > 0
                else 0.0
            ),
            "required_fields": list(self._required),
            "custom_rules": list(self._custom_rules.keys()),
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised in TrainingDataValidator")

    def __repr__(self) -> str:
        return (
            f"TrainingDataValidator(validated={self._validated_count}, "
            f"valid={self._valid_count}, invalid={self._invalid_count})"
        )


default_validator: TrainingDataValidator = TrainingDataValidator()
