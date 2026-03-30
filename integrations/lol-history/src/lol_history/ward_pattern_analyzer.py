"""
WardPatternAnalyzer — Analyze warding patterns and vision control habits.

Architecture (拿来主义):
  查看 **live_match_history_correlator.py** 的phase分割方式（EARLY_PHASE_END=900等）。
  实现 **WardPatternAnalyzer**，按时间段分析视野投入。

Location: integrations/lol-history/src/lol_history/ward_pattern_analyzer.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.ward_pattern_analyzer.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class WardPatternAnalyzer:
    """Analyze warding patterns from historical match data."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._games: List[Dict[str, Any]] = []

    def record_ward_data(
        self, wards_placed: int, wards_killed: int,
        control_wards: int, duration_minutes: float,
    ) -> None:
        self._games.append({
            "wards_placed": wards_placed, "wards_killed": wards_killed,
            "control_wards": control_wards, "duration_minutes": duration_minutes,
        })

    def get_summary(self) -> Dict[str, Any]:
        n = len(self._games)
        if n == 0:
            return {"total_games": 0, "avg_wards_per_min": 0.0,
                    "avg_wards_killed": 0.0, "control_ward_ratio": 0.0}
        wpm = [_safe_div(g["wards_placed"], g["duration_minutes"]) for g in self._games
               if g["duration_minutes"] > 0]
        total_placed = sum(g["wards_placed"] for g in self._games)
        total_control = sum(g["control_wards"] for g in self._games)
        total_killed = sum(g["wards_killed"] for g in self._games)
        return {
            "total_games": n,
            "avg_wards_per_min": sum(wpm) / len(wpm) if wpm else 0.0,
            "avg_wards_placed": total_placed / n,
            "avg_wards_killed": total_killed / n,
            "control_ward_ratio": _safe_div(total_control, total_placed),
        }

    def compute_vision_rating(self) -> str:
        s = self.get_summary()
        wpm = s["avg_wards_per_min"]
        if wpm >= 1.0:
            return "excellent"
        elif wpm >= 0.7:
            return "good"
        elif wpm >= 0.4:
            return "average"
        return "poor"

    def analyze_ward_timeline(self, timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
        phases: Dict[str, int] = {"early": 0, "mid": 0, "late": 0}
        for f in timeline:
            ts = f.get("timestamp", 0)
            w = f.get("wards_placed", 0)
            if ts <= 900:
                phases["early"] += w
            elif ts <= 1800:
                phases["mid"] += w
            else:
                phases["late"] += w
        return phases

    def to_dict(self) -> Dict[str, Any]:
        return self.get_summary()
