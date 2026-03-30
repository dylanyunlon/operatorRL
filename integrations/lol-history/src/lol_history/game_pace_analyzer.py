"""
GamePaceAnalyzer — Classify and analyze game pace from duration/kills.

Architecture (拿来主义):
  查看 **game_timeline_analyzer.py** 的时间轴切分和detect_gold_swings方式。
  实现 **GamePaceAnalyzer**，支持pace分类、phase分解和趋势分析。

Location: integrations/lol-history/src/lol_history/game_pace_analyzer.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.game_pace_analyzer.v1"

FAST_DURATION = 25
SLOW_DURATION = 35
FAST_KPM = 1.2
SLOW_KPM = 0.5


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class GamePaceAnalyzer:
    """Analyze game pace from duration, kills, and timeline data."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def classify_pace(self, duration_minutes: float, total_kills: int) -> Dict[str, Any]:
        kpm = _safe_div(total_kills, duration_minutes)
        if duration_minutes < FAST_DURATION and kpm > SLOW_KPM:
            pace = "fast"
        elif duration_minutes > SLOW_DURATION or kpm < SLOW_KPM:
            pace = "slow"
        else:
            pace = "normal"
        return {
            "pace": pace,
            "duration_minutes": duration_minutes,
            "total_kills": total_kills,
            "kills_per_minute": kpm,
        }

    def analyze_early_game(self, timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
        early = [f for f in timeline if f.get("timestamp", 0) <= 900]
        total_kills = sum(f.get("kills", 0) for f in early)
        return {
            "early_kill_rate": _safe_div(total_kills, len(early)) if early else 0.0,
            "early_frames": len(early),
            "total_early_kills": total_kills,
        }

    def analyze_batch(self, games: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not games:
            return {"avg_duration": 0.0, "avg_kills": 0.0, "total_games": 0, "avg_kpm": 0.0}
        n = len(games)
        durations = [g.get("duration_minutes", 0) for g in games]
        kills = [g.get("total_kills", 0) for g in games]
        avg_dur = sum(durations) / n
        avg_kills = sum(kills) / n
        return {
            "total_games": n,
            "avg_duration": avg_dur,
            "avg_kills": avg_kills,
            "avg_kpm": _safe_div(avg_kills, avg_dur),
        }

    def compute_pace_trend(self, games: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(games) < 2:
            return {"direction": "stable", "change": 0.0}
        first_half = games[:len(games) // 2]
        second_half = games[len(games) // 2:]
        kpm1 = _safe_div(
            sum(g.get("total_kills", 0) for g in first_half),
            sum(g.get("duration_minutes", 1) for g in first_half),
        )
        kpm2 = _safe_div(
            sum(g.get("total_kills", 0) for g in second_half),
            sum(g.get("duration_minutes", 1) for g in second_half),
        )
        diff = kpm2 - kpm1
        if diff > 0.1:
            direction = "faster"
        elif diff < -0.1:
            direction = "slower"
        else:
            direction = "stable"
        return {"direction": direction, "change": diff, "kpm_early": kpm1, "kpm_late": kpm2}

    def phase_breakdown(self, timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
        phases: Dict[str, List] = {"early": [], "mid": [], "late": []}
        for f in timeline:
            ts = f.get("timestamp", 0)
            if ts <= 900:
                phases["early"].append(f)
            elif ts <= 1800:
                phases["mid"].append(f)
            else:
                phases["late"].append(f)
        result = {}
        for phase, frames in phases.items():
            kills = sum(fr.get("kills", 0) for fr in frames)
            result[phase] = {"frames": len(frames), "kills": kills}
        return result
