"""
Danger Zone Detector — Computes danger levels from enemy positions and vision.

Calculates danger at player position based on distance to enemies,
ward coverage, and number of threats. Provides safe retreat direction.

Location: integrations/lol/src/lol_agent/danger_zone_detector.py

Reference (拿来主义):
  - LeagueAI/LeagueAI_helper.py: detection class (x,y bounding boxes → distance)
  - dota2bot-OpenHyperAI: mode_retreat.lua danger assessment
  - integrations/lol/src/lol_agent/opponent_history_merger.py: threat scoring
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.danger_zone_detector.v1"

_DANGER_RADIUS: float = 3000.0  # Units within which enemies are considered dangerous
_WARD_MITIGATION: float = 0.3   # Danger reduction per nearby ward


class DangerZoneDetector:
    """Detects danger zones based on enemy positions and ward coverage.

    Computes a danger_level score, is_safe flag, and safe_direction
    for retreat recommendations.

    Attributes:
        danger_threshold: Danger level above which is_safe = False.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, danger_threshold: float = 5.0) -> None:
        self.danger_threshold: float = danger_threshold

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def assess(
        self,
        my_position: tuple[float, float],
        enemies: list[dict[str, Any]],
        wards: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Assess danger at current position.

        Args:
            my_position: (x, y) tuple of player position.
            enemies: List of dicts with 'position' (x,y) and 'champion'.
            wards: List of dicts with 'position' (x,y).

        Returns:
            Dict with danger_level, is_safe, safe_direction.
        """
        mx, my = my_position

        # Compute danger from each enemy based on distance
        danger = 0.0
        threat_vectors: list[tuple[float, float]] = []

        for enemy in enemies:
            ex, ey = enemy.get("position", (0, 0))
            dist = math.sqrt((ex - mx) ** 2 + (ey - my) ** 2)
            if dist < _DANGER_RADIUS:
                # Closer = more dangerous (inverse distance)
                enemy_danger = max(0.0, (_DANGER_RADIUS - dist) / _DANGER_RADIUS) * 5.0
                danger += enemy_danger
                # Track direction of threat
                if dist > 0:
                    threat_vectors.append(((ex - mx) / dist, (ey - my) / dist))

        # Ward mitigation: each nearby ward reduces danger
        for ward in wards:
            wx, wy = ward.get("position", (0, 0))
            ward_dist = math.sqrt((wx - mx) ** 2 + (wy - my) ** 2)
            if ward_dist < _DANGER_RADIUS:
                danger -= _WARD_MITIGATION

        danger = max(0.0, danger)

        # Compute safe direction (away from threats)
        if threat_vectors:
            avg_tx = sum(v[0] for v in threat_vectors) / len(threat_vectors)
            avg_ty = sum(v[1] for v in threat_vectors) / len(threat_vectors)
            # Safe = opposite of average threat direction
            safe_direction = (-avg_tx, -avg_ty)
        else:
            safe_direction = (0.0, 0.0)

        is_safe = danger < self.danger_threshold

        result = {
            "danger_level": danger,
            "is_safe": is_safe,
            "safe_direction": safe_direction,
            "threat_count": len(threat_vectors),
        }

        self._fire_evolution("danger_assessed", {
            "danger_level": danger,
            "is_safe": is_safe,
            "threat_count": len(threat_vectors),
        })

        return result

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
