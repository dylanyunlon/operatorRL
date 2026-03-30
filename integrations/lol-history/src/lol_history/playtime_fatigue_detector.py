"""
PlaytimeFatigueDetector — Detect player fatigue from session length and performance decay.

Architecture (拿来主义):
  查看 **tilt_detector.py** 的心态建模模式。从 **opponent_behavior_modeler.py** 的
  detect_tilt_indicators开始。实现 **PlaytimeFatigueDetector**，支持session记录、
  performance decay检测和休息建议。

Location: integrations/lol-history/src/lol_history/playtime_fatigue_detector.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.playtime_fatigue_detector.v1"

FATIGUE_GAME_THRESHOLD = 7
FATIGUE_MINUTE_THRESHOLD = 210  # 3.5 hours
FATIGUE_WINRATE_THRESHOLD = 0.35


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class PlaytimeFatigueDetector:
    """Detect player fatigue from session patterns."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._sessions: List[Dict[str, Any]] = []
        self._game_sequence: List[Dict[str, Any]] = []

    def record_session(self, games_played: int, total_minutes: float,
                       wins: int, losses: int) -> None:
        self._sessions.append({
            "games_played": games_played,
            "total_minutes": total_minutes,
            "wins": wins,
            "losses": losses,
            "winrate": _safe_div(wins, wins + losses),
        })

    def record_game_sequence(self, games: List[Dict[str, Any]]) -> None:
        self._game_sequence = list(games)

    def get_summary(self) -> Dict[str, Any]:
        n = len(self._sessions)
        if n == 0:
            return {"total_sessions": 0}
        return {
            "total_sessions": n,
            "avg_games_per_session": sum(s["games_played"] for s in self._sessions) / n,
            "avg_session_minutes": sum(s["total_minutes"] for s in self._sessions) / n,
            "avg_session_winrate": sum(s["winrate"] for s in self._sessions) / n,
        }

    def detect_fatigue(self) -> Dict[str, Any]:
        if not self._sessions:
            return {"is_fatigued": False, "reason": "no_data"}
        last = self._sessions[-1]
        is_fatigued = (
            last["games_played"] >= FATIGUE_GAME_THRESHOLD
            or last["total_minutes"] >= FATIGUE_MINUTE_THRESHOLD
            or (last["games_played"] >= 5 and last["winrate"] < FATIGUE_WINRATE_THRESHOLD)
        )
        reasons = []
        if last["games_played"] >= FATIGUE_GAME_THRESHOLD:
            reasons.append("too_many_games")
        if last["total_minutes"] >= FATIGUE_MINUTE_THRESHOLD:
            reasons.append("long_session")
        if last["games_played"] >= 5 and last["winrate"] < FATIGUE_WINRATE_THRESHOLD:
            reasons.append("declining_winrate")
        return {"is_fatigued": is_fatigued, "reasons": reasons}

    def compute_performance_decay(self) -> Dict[str, Any]:
        if len(self._game_sequence) < 3:
            return {"trend": "stable", "slope": 0.0}
        first_half = self._game_sequence[:len(self._game_sequence) // 2]
        second_half = self._game_sequence[len(self._game_sequence) // 2:]
        kda1 = sum(g.get("kda", 0) for g in first_half) / len(first_half)
        kda2 = sum(g.get("kda", 0) for g in second_half) / len(second_half)
        diff = kda2 - kda1
        if diff < -0.5:
            trend = "declining"
        elif diff > 0.5:
            trend = "improving"
        else:
            trend = "stable"
        return {"trend": trend, "slope": diff, "kda_early": kda1, "kda_late": kda2}

    def recommend_break(self) -> Dict[str, Any]:
        fatigue = self.detect_fatigue()
        decay = self.compute_performance_decay()
        should_break = fatigue["is_fatigued"] or decay["trend"] == "declining"
        return {
            "should_break": should_break,
            "fatigue": fatigue,
            "performance": decay,
        }

    def get_optimal_session_length(self) -> Dict[str, Any]:
        if not self._sessions:
            return {"recommended_games": 5, "confidence": 0.0}
        best_wr = 0.0
        best_games = 5
        for s in self._sessions:
            if s["winrate"] > best_wr:
                best_wr = s["winrate"]
                best_games = s["games_played"]
        return {"recommended_games": best_games, "best_winrate": best_wr}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.get_summary(),
            "fatigue": self.detect_fatigue(),
        }
