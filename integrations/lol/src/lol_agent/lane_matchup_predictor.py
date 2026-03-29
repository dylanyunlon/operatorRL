"""
Lane Matchup Predictor — Predict lane outcome from historical matchups.
Location: integrations/lol/src/lol_agent/lane_matchup_predictor.py
Reference: leagueoflegends-optimizer matchup analysis, Seraphine match details
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.lane_matchup_predictor.v1"

class LaneMatchupPredictor:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def predict(self, my_champion: str, enemy_champion: str,
                history: list[dict[str, Any]]) -> dict[str, Any]:
        relevant = [h for h in history
                    if h.get("my_champion") == my_champion and h.get("enemy_champion") == enemy_champion]
        n = len(relevant)
        if n == 0:
            self._fire("predict_empty", {"my": my_champion, "enemy": enemy_champion})
            return {"win_probability": 0.5, "confidence": 0.0, "sample_size": 0,
                    "tips": [], "avg_cs_diff": 0.0, "avg_kill_diff": 0.0}

        wins = sum(1 for h in relevant if h.get("win_lane"))
        win_prob = wins / n
        confidence = min(1.0, n / 10.0)

        cs_diffs = [h["cs_diff"] for h in relevant if "cs_diff" in h]
        kill_diffs = [h["kill_diff"] for h in relevant if "kill_diff" in h]
        avg_cs = sum(cs_diffs) / len(cs_diffs) if cs_diffs else 0.0
        avg_kill = sum(kill_diffs) / len(kill_diffs) if kill_diffs else 0.0

        tips = []
        if win_prob > 0.6:
            tips.append(f"Historically strong matchup ({win_prob:.0%} win rate)")
        elif win_prob < 0.4:
            tips.append(f"Historically weak matchup ({win_prob:.0%} win rate) — play safe")
        if avg_cs > 10:
            tips.append("You typically get a CS advantage — focus on farming")
        if avg_kill > 0.5:
            tips.append("You tend to get kills in this matchup — look for trades")

        self._fire("predict_complete", {"my": my_champion, "enemy": enemy_champion, "n": n})
        return {"win_probability": win_prob, "confidence": confidence, "sample_size": n,
                "tips": tips, "avg_cs_diff": avg_cs, "avg_kill_diff": avg_kill}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
