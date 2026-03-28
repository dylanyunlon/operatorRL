"""
Curriculum Manager — Difficulty progression training.

Provides level registration, performance-based advancement,
and current-level config access. Adapted from DI-star's
guided training approach and PARL's curriculum patterns.

Location: agentlightning/trainer/curriculum_manager.py

Reference: DI-star guided training, PARL benchmark configs.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.trainer.curriculum_manager.v1"


class CurriculumManager:
    """Manages difficulty progression for agent training.

    Registers difficulty levels with performance thresholds.
    Advances when the agent consistently meets the current threshold.
    """

    def __init__(self) -> None:
        self.current_level: int = 0
        self.levels: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_level(
        self,
        level: int,
        name: str,
        threshold: float,
        **config: Any,
    ) -> None:
        """Register a curriculum level.

        Args:
            level: Level index.
            name: Level name.
            threshold: Performance threshold to advance past this level.
            **config: Additional level config.
        """
        entry = {"level": level, "name": name, "threshold": threshold}
        entry.update(config)

        # Insert at correct position
        while len(self.levels) <= level:
            self.levels.append({})
        self.levels[level] = entry

    def should_advance(self, current_performance: float) -> bool:
        """Check if agent should advance to next level.

        Args:
            current_performance: Current performance metric.

        Returns:
            True if performance exceeds current level threshold.
        """
        if self.current_level >= len(self.levels):
            return False
        threshold = self.levels[self.current_level].get("threshold", 1.0)
        return current_performance >= threshold

    def advance_level(self) -> None:
        """Advance to the next curriculum level."""
        max_level = len(self.levels) - 1 if self.levels else 0
        if self.current_level < max_level:
            self.current_level += 1
        elif not self.levels:
            self.current_level = 0
        # If already at max, stay there

        self._fire_evolution({
            "event": "level_advanced",
            "current_level": self.current_level,
        })

    def get_current_config(self) -> Optional[dict[str, Any]]:
        """Get configuration for current level.

        Returns:
            Level config dict or None/empty if no levels registered.
        """
        if not self.levels or self.current_level >= len(self.levels):
            return {}
        config = self.levels[self.current_level]
        return config if config else {}

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
