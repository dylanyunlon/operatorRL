"""
HistoricalMatchupWinPredictor — Predicts win probability using historical matchup data.

Architecture (拿来主义):
  historical_matchup_predictor.py — matchup prediction patterns
  combat_outcome_predictor.py — outcome prediction with confidence

Location: integrations/lol-history/src/lol_history/historical_matchup_win_predictor.py

Design Notes (Knuth-level critique):
  User:
    - predict() returns a win probability with confidence interval.
    - Factors in champion matchup, player history, and team composition.
  System:
    - Bayesian prior of 0.5 (no info = coin flip) ensures graceful degradation.
    - Sample size directly affects confidence — small N → wide interval.
"""
from __future__ import annotations
import logging, math, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.historical_matchup_win_predictor.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class HistoricalMatchupWinPredictor:
    """Predicts win probability from historical matchup data.

    Public API: predict, predict_lane, add_matchup_data, get_stats
    """
    def __init__(self, prior_win_rate: float = 0.5, min_samples: int = 3) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._prior = prior_win_rate
        self._min_samples = min_samples
        self._matchup_db: Dict[str, Dict[str, Any]] = {}
        self._predict_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _matchup_key(self, champ_a: int, champ_b: int) -> str:
        return f"{champ_a}v{champ_b}"

    def add_matchup_data(self, champ_a: int, champ_b: int, wins_a: int, total: int,
                         role: str = "any") -> Dict[str, Any]:
        """Add historical matchup data.

        Args:
            champ_a: Our champion ID.
            champ_b: Opponent champion ID.
            wins_a: Number of wins for champ_a.
            total: Total games in this matchup.
            role: Lane role context (top/mid/bot/jungle/support/any).
        """
        self._op_count += 1
        key = f"{self._matchup_key(champ_a, champ_b)}:{role}"
        if key in self._matchup_db:
            existing = self._matchup_db[key]
            existing["wins"] += wins_a
            existing["total"] += total
        else:
            self._matchup_db[key] = {"champ_a": champ_a, "champ_b": champ_b,
                                      "role": role, "wins": wins_a, "total": total}
        return {"status": "ok", "key": key, "total_matchups": len(self._matchup_db)}

    def predict(self, our_team: List[Dict], enemy_team: List[Dict]) -> Dict[str, Any]:
        """Predict overall win probability for our team.

        Args:
            our_team: List of dicts with champion_id, role, puuid, recent_win_rate.
            enemy_team: Same structure for opponents.
        """
        self._op_count += 1
        self._predict_count += 1
        t0 = time.time()

        lane_predictions = []
        total_weight = 0.0
        weighted_sum = 0.0

        for our in our_team:
            our_champ = our.get("champion_id", 0)
            our_role = our.get("role", "any")
            our_wr = our.get("recent_win_rate", self._prior)
            best_match = None

            for enemy in enemy_team:
                enemy_champ = enemy.get("champion_id", 0)
                enemy_role = enemy.get("role", "any")
                if our_role == enemy_role or our_role == "any":
                    lane_pred = self._predict_lane_matchup(our_champ, enemy_champ, our_role, our_wr)
                    lane_predictions.append({
                        "our_champ": our_champ, "enemy_champ": enemy_champ,
                        "role": our_role, **lane_pred
                    })
                    weight = lane_pred.get("confidence", 0.5)
                    weighted_sum += lane_pred["win_probability"] * weight
                    total_weight += weight
                    best_match = True
                    break

            if not best_match:
                lane_predictions.append({"our_champ": our_champ, "role": our_role,
                                          "win_probability": our_wr, "confidence": 0.3})
                weighted_sum += our_wr * 0.3
                total_weight += 0.3

        overall_prob = _safe_div(weighted_sum, total_weight, self._prior)
        overall_prob = round(min(max(overall_prob, 0.01), 0.99), 4)

        # Confidence interval (Wilson score approximation)
        n = max(sum(1 for lp in lane_predictions if lp.get("confidence", 0) > 0.4), 1)
        z = 1.96
        ci_half = z * math.sqrt(_safe_div(overall_prob * (1 - overall_prob), n))
        ci_lower = round(max(overall_prob - ci_half, 0.0), 4)
        ci_upper = round(min(overall_prob + ci_half, 1.0), 4)

        elapsed = round((time.time() - t0) * 1000, 1)
        result = {
            "status": "ok", "win_probability": overall_prob,
            "confidence_interval": [ci_lower, ci_upper],
            "lane_predictions": lane_predictions,
            "elapsed_ms": elapsed,
        }
        self._fire("predicted", {"win_probability": overall_prob})
        return result

    def _predict_lane_matchup(self, champ_a: int, champ_b: int,
                               role: str, fallback_wr: float) -> Dict[str, Any]:
        """Predict single lane matchup."""
        # Try role-specific first, then "any"
        for r in [role, "any"]:
            key = f"{self._matchup_key(champ_a, champ_b)}:{r}"
            if key in self._matchup_db:
                data = self._matchup_db[key]
                n = data["total"]
                if n >= self._min_samples:
                    raw_wr = _safe_div(data["wins"], n)
                    # Bayesian shrinkage toward prior
                    k = 10  # prior strength
                    adjusted = (raw_wr * n + self._prior * k) / (n + k)
                    confidence = min(n / 30.0, 1.0)
                    return {"win_probability": round(adjusted, 4), "confidence": round(confidence, 4),
                            "sample_size": n, "raw_win_rate": round(raw_wr, 4)}

        return {"win_probability": fallback_wr, "confidence": 0.2, "sample_size": 0, "raw_win_rate": None}

    def predict_lane(self, our_champ: int, enemy_champ: int, role: str = "any") -> Dict[str, Any]:
        """Quick single-lane prediction."""
        self._op_count += 1
        result = self._predict_lane_matchup(our_champ, enemy_champ, role, self._prior)
        return {"status": "ok", **result}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "matchups_stored": len(self._matchup_db),
                "predictions": self._predict_count}
