"""
RolePerformanceTracker — Track per-role (TOP/JG/MID/ADC/SUP) performance stats.

Architecture (拿来主义):
  查看 **player_profiler.py** 上现有多维聚合方式。从 **history_match_aggregator.py** 的
  by_champion分组开始——改为by_role分组。实现 **RolePerformanceTracker**。

Location: integrations/lol-history/src/lol_history/role_performance_tracker.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.role_performance_tracker.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    return (k + a) / max(d, 1)


class RolePerformanceTracker:
    """Track performance stats per role."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._roles: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def record_game(
        self, role: str, won: bool, kills: int, deaths: int,
        assists: int, cs: int, gold: int, duration_minutes: float,
    ) -> None:
        self._roles[role].append({
            "won": won, "kills": kills, "deaths": deaths, "assists": assists,
            "cs": cs, "gold": gold, "duration_minutes": duration_minutes,
        })
        self._fire("game_recorded", {"role": role, "won": won})

    def get_role_stats(self, role: str) -> Dict[str, Any]:
        games = self._roles.get(role, [])
        n = len(games)
        if n == 0:
            return {"role": role, "games": 0, "winrate": 0.0, "avg_kda": 0.0,
                    "avg_cs_per_min": 0.0, "avg_gold_per_min": 0.0, "avg_deaths": 0.0}
        wins = sum(1 for g in games if g["won"])
        kdas = [_kda(g["kills"], g["deaths"], g["assists"]) for g in games]
        cs_pm = [_safe_div(g["cs"], g["duration_minutes"]) for g in games if g["duration_minutes"] > 0]
        gpm = [_safe_div(g["gold"], g["duration_minutes"]) for g in games if g["duration_minutes"] > 0]
        deaths = [g["deaths"] for g in games]
        return {
            "role": role, "games": n, "winrate": wins / n,
            "avg_kda": sum(kdas) / n,
            "avg_cs_per_min": sum(cs_pm) / len(cs_pm) if cs_pm else 0.0,
            "avg_gold_per_min": sum(gpm) / len(gpm) if gpm else 0.0,
            "avg_deaths": sum(deaths) / n,
        }

    def get_best_role(self) -> Dict[str, Any]:
        if not self._roles:
            return {"role": "NONE", "games": 0, "winrate": 0.0}
        stats = [self.get_role_stats(r) for r in self._roles]
        stats.sort(key=lambda x: (x["winrate"], x["avg_kda"]), reverse=True)
        return stats[0]

    def get_all_roles_summary(self) -> Dict[str, Dict[str, Any]]:
        return {r: self.get_role_stats(r) for r in self._roles}

    def to_dict(self) -> Dict[str, Any]:
        return self.get_all_roles_summary()

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback:
            self.evolution_callback({
                "type": event_type, "key": _EVOLUTION_KEY,
                "timestamp": time.time(), **data,
            })
