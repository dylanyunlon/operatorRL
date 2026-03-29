"""
Dota2 History Aggregator — OpenDota API historical data aggregation.

Location: integrations/dota2/src/dota2_agent/dota2_history_aggregator.py

Reference (拿来主義):
  - dota2bot-OpenHyperAI: game state analysis
  - Seraphine/tools.py: parseSummonerData aggregation pattern → Dota2 adaptation
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.dota2.dota2_history_aggregator.v1"

class Dota2HistoryAggregator:
    """Aggregate Dota2 match history statistics."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def aggregate(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(matches)
        wins = sum(1 for m in matches if m.get("win", False))
        kills = sum(m.get("kills", 0) for m in matches)
        deaths = sum(m.get("deaths", 0) for m in matches)
        assists = sum(m.get("assists", 0) for m in matches)
        kda = (kills + assists) / max(deaths, 1)
        gpm = sum(m.get("gold_per_min", 0) for m in matches) / max(total, 1)
        xpm = sum(m.get("xp_per_min", 0) for m in matches) / max(total, 1)
        self._fire_evolution("aggregated", {"total": total})
        return {"total_games": total, "wins": wins, "losses": total - wins, "winrate": wins / max(total, 1), "kda": kda, "avg_gpm": gpm, "avg_xpm": xpm}

    def aggregate_by_hero(self, matches: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        by_hero: dict[int, dict[str, Any]] = {}
        for m in matches:
            hid = m.get("hero_id", 0)
            if hid not in by_hero:
                by_hero[hid] = {"total": 0, "wins": 0}
            by_hero[hid]["total"] += 1
            if m.get("win", False):
                by_hero[hid]["wins"] += 1
        return by_hero

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
