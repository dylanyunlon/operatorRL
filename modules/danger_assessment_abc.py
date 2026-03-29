"""
Danger Assessment ABC — Cross-game unified danger detection interface.

Provides abstract base class for all game-specific danger detectors,
enabling unified assess/safe_direction/is_safe access across games.

Location: modules/danger_assessment_abc.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/danger_zone_detector.py: danger assessment
  - modules/game_bridge_abc.py: ABC pattern
  - LeagueAI/helper.py: distance-based danger calculation
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

_EVOLUTION_KEY: str = "modules.danger_assessment_abc.v1"


class DangerAssessmentABC(ABC):
    """Abstract base class for game-specific danger assessment.

    All danger assessors must provide:
    - assess: compute danger level at position
    - safe_direction: find safest movement direction
    - is_safe: check if position is safe
    """

    @abstractmethod
    def assess(
        self, position: Any, enemies: Any, vision: Any
    ) -> float:
        """Compute danger level at a given position.

        Args:
            position: Current position (format varies by game).
            enemies: Known enemy positions/data.
            vision: Vision/fog-of-war data.

        Returns:
            Danger level float (0.0 = safe, 1.0 = maximum danger).
        """
        ...

    @abstractmethod
    def safe_direction(
        self, position: Any, enemies: Any
    ) -> dict[str, Any]:
        """Find the safest direction to move.

        Args:
            position: Current position.
            enemies: Known enemy positions.

        Returns:
            Dict with direction components.
        """
        ...

    @abstractmethod
    def is_safe(
        self, position: Any, enemies: Any, vision: Any
    ) -> bool:
        """Check if current position is safe.

        Args:
            position: Current position.
            enemies: Known enemy positions.
            vision: Vision data.

        Returns:
            True if position is safe.
        """
        ...
