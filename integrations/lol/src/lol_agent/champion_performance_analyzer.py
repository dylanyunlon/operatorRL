"""
Champion Performance Analyzer — Player stat analysis and trending.

Computes KDA, CS/min, damage share, ward efficiency, and trend
analysis across historical match data.

Location: integrations/lol/src/lol_agent/champion_performance_analyzer.py

Reference (拿来主义):
  - leagueoflegends-optimizer: stats pipeline (article5.md)
  - integrations/lol-history/src/lol_history/match_analyzer.py: analysis pattern
  - Seraphine/app/lol/tools.py: stat calculation utilities
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.champion_performance_analyzer.v1"


class ChampionPerformanceAnalyzer:
    """Analyzes champion/player performance from match statistics.

    Computes standard metrics (KDA, CS/min, damage, wards) and
    provides trend analysis across multiple matches.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def compute_kda(self, kills: int, deaths: int, assists: int) -> float:
        """Compute KDA ratio.

        Args:
            kills: Total kills.
            deaths: Total deaths.
            assists: Total assists.

        Returns:
            KDA ratio. Returns (kills+assists) if deaths == 0 (perfect KDA).
        """
        if deaths == 0:
            return float(kills + assists) if (kills + assists) > 0 else 0.0
        return (kills + assists) / deaths

    def compute_cs_per_minute(
        self, creep_score: int, game_time_seconds: float
    ) -> float:
        """Compute CS per minute.

        Args:
            creep_score: Total creep score.
            game_time_seconds: Game duration in seconds.

        Returns:
            CS/min. Returns 0 if game_time is 0.
        """
        if game_time_seconds <= 0:
            return 0.0
        return creep_score / (game_time_seconds / 60.0)

    def analyze(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Produce a full performance report from a single match's stats.

        Args:
            stats: Dict with kills, deaths, assists, creepScore,
                   damageDealt, wardsPlaced, game_time_seconds.

        Returns:
            Report dict with kda, cs_per_min, damage, wards_per_min.
        """
        kills = stats.get("kills", 0)
        deaths = stats.get("deaths", 0)
        assists = stats.get("assists", 0)
        cs = stats.get("creepScore", 0)
        damage = stats.get("damageDealt", 0)
        wards = stats.get("wardsPlaced", 0)
        game_time = stats.get("game_time_seconds", 0)

        kda = self.compute_kda(kills, deaths, assists)
        cs_per_min = self.compute_cs_per_minute(cs, game_time)
        wards_per_min = (wards / (game_time / 60.0)) if game_time > 0 else 0.0

        report = {
            "kda": kda,
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "cs_per_min": cs_per_min,
            "damage": damage,
            "wards_per_min": wards_per_min,
            "game_time_seconds": game_time,
        }

        self._fire_evolution("performance_analyzed", {"kda": kda})
        return report

    def compute_trend(
        self, history: list[dict[str, Any]]
    ) -> dict[str, float]:
        """Compute performance trend across a list of match stats.

        Uses simple linear slope estimation for KDA and CS/min.

        Args:
            history: List of stat dicts (same format as analyze() input).

        Returns:
            Dict with kda_trend and cs_trend (positive = improving).
        """
        if len(history) < 2:
            return {"kda_trend": 0.0, "cs_trend": 0.0}

        kdas = []
        cspm = []
        for stats in history:
            k = stats.get("kills", 0)
            d = stats.get("deaths", 0)
            a = stats.get("assists", 0)
            cs = stats.get("creepScore", 0)
            gt = stats.get("game_time_seconds", 1)
            kdas.append(self.compute_kda(k, d, a))
            cspm.append(self.compute_cs_per_minute(cs, gt))

        # Simple slope: (last - first) / (n - 1)
        n = len(kdas)
        kda_trend = (kdas[-1] - kdas[0]) / max(n - 1, 1)
        cs_trend = (cspm[-1] - cspm[0]) / max(n - 1, 1)

        return {"kda_trend": kda_trend, "cs_trend": cs_trend}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
