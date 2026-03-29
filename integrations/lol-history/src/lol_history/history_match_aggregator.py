"""
History Match Aggregator — Aggregate match stats across champion pool.

Computes total games, winrate, avg KDA, gold/cs per minute, 
per-champion breakdown, and most played champion.

Location: integrations/lol-history/src/lol_history/history_match_aggregator.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: match history response parsing
  - leagueoflegends-optimizer: article5.md feature engineering (f1,f2,f3)
  - integrations/lol-history/src/lol_history/match_analyzer.py: stat aggregation
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.history_match_aggregator.v1"


class HistoryMatchAggregator:
    """Aggregate match statistics across a player's champion pool."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def aggregate(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        if not matches:
            return {"total_games": 0, "avg_kda": 0.0, "winrate": 0.0,
                    "avg_gold_per_min": 0.0, "avg_cs_per_min": 0.0,
                    "by_champion": {}, "most_played": "", "recommendations": []}

        total = len(matches)
        wins = sum(1 for m in matches if m.get("win"))
        kdas, gpm_list, cspm_list = [], [], []
        by_champ: dict[str, dict[str, Any]] = defaultdict(lambda: {"games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0})

        for m in matches:
            k, d, a = m.get("kills", 0), m.get("deaths", 0), m.get("assists", 0)
            kda = (k + a) / max(1, d)
            kdas.append(kda)

            dur = m.get("duration_minutes", 0)
            gold = m.get("gold", 0)
            cs = m.get("cs", 0)
            if dur and dur > 0:
                gpm_list.append(gold / dur)
                cspm_list.append(cs / dur)

            champ = m.get("champion", "unknown")
            by_champ[champ]["games"] += 1
            if m.get("win"):
                by_champ[champ]["wins"] += 1
            by_champ[champ]["kills"] += k
            by_champ[champ]["deaths"] += d
            by_champ[champ]["assists"] += a

        most_played = max(by_champ, key=lambda c: by_champ[c]["games"]) if by_champ else ""

        result = {
            "total_games": total,
            "winrate": wins / total if total else 0.0,
            "avg_kda": sum(kdas) / len(kdas) if kdas else 0.0,
            "avg_gold_per_min": sum(gpm_list) / len(gpm_list) if gpm_list else 0.0,
            "avg_cs_per_min": sum(cspm_list) / len(cspm_list) if cspm_list else 0.0,
            "by_champion": dict(by_champ),
            "most_played": most_played,
        }

        self._fire_evolution("aggregation_complete", {"total_games": total, "winrate": result["winrate"]})
        return result

    def _fire_evolution(self, event_type: str, data: dict[str, Any]) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": event_type, "key": _EVOLUTION_KEY, "timestamp": time.time(), **data})
