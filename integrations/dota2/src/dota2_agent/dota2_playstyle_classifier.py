"""
Dota2 Playstyle Classifier — Attack/defense/push/roam classification.

Location: integrations/dota2/src/dota2_agent/dota2_playstyle_classifier.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.dota2.dota2_playstyle_classifier.v1"

class Dota2PlaystyleClassifier:
    """Classify player playstyle from match statistics."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def classify(self, stats: dict[str, Any]) -> dict[str, Any]:
        kills_avg = stats.get("avg_kills", 0)
        deaths_avg = stats.get("avg_deaths", 0)
        assists_avg = stats.get("avg_assists", 0)
        tower_dmg = stats.get("avg_tower_damage", 0)
        hero_dmg = stats.get("avg_hero_damage", 0)

        scores = {
            "aggressive": min(kills_avg / 10.0, 1.0) * 0.6 + min(hero_dmg / 30000, 1.0) * 0.4,
            "defensive": (1.0 - min(deaths_avg / 10.0, 1.0)) * 0.5 + min(assists_avg / 15.0, 1.0) * 0.5,
            "push": min(tower_dmg / 10000, 1.0),
            "roam": min(assists_avg / 15.0, 1.0) * 0.7 + min(kills_avg / 10.0, 1.0) * 0.3,
        }
        primary = max(scores, key=scores.get)
        self._fire_evolution("classified", {"primary": primary})
        return {"primary_style": primary, "scores": scores}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
