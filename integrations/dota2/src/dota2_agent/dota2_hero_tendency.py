"""
Dota2 Hero Tendency — Hero-specific play pattern analysis.

Location: integrations/dota2/src/dota2_agent/dota2_hero_tendency.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.dota2.dota2_hero_tendency.v1"

class Dota2HeroTendency:
    """Analyze hero-specific tendencies from match history."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def analyze(self, hero_id: int, matches: list[dict[str, Any]]) -> dict[str, Any]:
        relevant = [m for m in matches if m.get("hero_id") == hero_id]
        total = len(relevant)
        if total == 0:
            return {"hero_id": hero_id, "total": 0, "winrate": 0.5, "avg_kda": 0.0, "tendency": "unknown"}
        wins = sum(1 for m in relevant if m.get("win", False))
        kills = sum(m.get("kills", 0) for m in relevant)
        deaths = max(sum(m.get("deaths", 0) for m in relevant), 1)
        assists = sum(m.get("assists", 0) for m in relevant)
        kda = (kills + assists) / deaths
        aggression = kills / max(total, 1)
        tendency = "aggressive" if aggression > 8 else ("passive" if aggression < 3 else "balanced")
        self._fire_evolution("tendency_analyzed", {"hero_id": hero_id, "tendency": tendency})
        return {"hero_id": hero_id, "total": total, "winrate": wins / total, "avg_kda": kda, "tendency": tendency}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
