"""
History Strategy Fuser — Fuse historical profiles into live strategy.
Location: integrations/lol/src/lol_agent/history_strategy_fuser.py
Reference: opponent_history_merger, lol_strategy_advisor, DI-star rl_learner
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.history_strategy_fuser.v1"

class HistoryStrategyFuser:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def fuse(self, live_state: dict[str, Any], opponent_profile: dict[str, Any],
             historical_tendencies: dict[str, Any]) -> dict[str, Any]:
        recs: list[dict[str, Any]] = []
        game_time = live_state.get("game_time", 0)
        is_early = game_time < 900  # < 15 min
        games = opponent_profile.get("games", 0)
        confidence = min(1.0, games / 20.0) if games > 0 else 0.0

        # Aggressive opponent analysis
        agg = opponent_profile.get("aggression_score", 0)
        if agg > 0.6:
            if is_early and historical_tendencies.get("early_game_tendency", 0) > 0.5:
                recs.append({"action": "play_safe", "reason": "Opponent is highly aggressive with strong early game — play cautious until mid",
                             "priority": 1})
            elif not is_early:
                recs.append({"action": "punish", "reason": "Aggressive opponent in late game — punish overextension",
                             "priority": 2})

        # Weak CS opponent
        avg_cs = opponent_profile.get("avg_cs_per_min", 0)
        if 0 < avg_cs < 5.5:
            recs.append({"action": "outfarm", "reason": "Opponent has low CS/min — focus on farm advantage",
                         "priority": 3})

        # Tilt detection
        tilt = opponent_profile.get("tilt_score", 0)
        if tilt > 0.5:
            recs.append({"action": "pressure", "reason": f"Opponent appears tilted (score={tilt:.1f}) — apply constant pressure",
                         "priority": 1})

        # Death rate weakness
        dr = opponent_profile.get("death_rate", 0)
        if dr > 0.3:
            recs.append({"action": "all_in", "reason": "High death rate opponent — look for all-in opportunities",
                         "priority": 2})

        # Sort by priority
        recs.sort(key=lambda r: r["priority"])

        self._fire("fuse_complete", {"recs": len(recs), "confidence": confidence})
        return {"recommendations": recs, "confidence": confidence}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
