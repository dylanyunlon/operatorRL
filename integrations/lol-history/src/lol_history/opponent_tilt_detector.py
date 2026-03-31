"""
OpponentTiltDetector — Detects opponent tilt state from recent match patterns.

Architecture (拿来主义):
  playtime_fatigue_detector.py — fatigue/tilt detection patterns
  streak_momentum_analyzer.py — streak analysis

Location: integrations/lol-history/src/lol_history/opponent_tilt_detector.py

Design Notes (Knuth-level critique):
  User:
    - detect() returns tilt probability + contributing factors for an opponent.
    - Tilt indicators: losing streaks, rising deaths, rage-quits, champion hopping.
  System:
    - Only analyzes recent sessions (last 10 games) for temporal relevance.
    - Composite scoring prevents single-factor false positives.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.opponent_tilt_detector.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class OpponentTiltDetector:
    """Detects opponent tilt from recent match history.

    Public API: detect, detect_batch, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._detect_count = 0
        self._thresholds = {
            "losing_streak_min": 3,
            "death_spike_ratio": 1.5,
            "champion_hop_min": 4,
            "short_game_min": 2,
            "rage_death_threshold": 8,
        }

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_thresholds(self, thresholds: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._thresholds.update(thresholds)
        return {"status": "ok", "thresholds": dict(self._thresholds)}

    def detect(self, puuid: str, recent_matches: List[Dict[str, Any]],
               summoner_name: str = "") -> Dict[str, Any]:
        """Detect tilt state from recent match history.

        Args:
            recent_matches: Most recent matches first (up to ~10).

        Returns:
            Dict with tilt_probability (0-1), factors, and recommendation.
        """
        self._op_count += 1
        self._detect_count += 1

        if not recent_matches:
            return {"puuid": puuid, "name": summoner_name, "tilt_probability": 0.0,
                    "factors": [], "note": "no_data"}

        matches = recent_matches[:10]
        factors = []
        tilt_score = 0.0

        # Factor 1: Losing streak
        consecutive_losses = 0
        for m in matches:
            if not m.get("win"):
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= self._thresholds["losing_streak_min"]:
            weight = min(consecutive_losses / 5.0, 1.0) * 0.3
            tilt_score += weight
            factors.append({"factor": "losing_streak", "value": consecutive_losses,
                            "weight": round(weight, 3)})

        # Factor 2: Death spike (recent deaths much higher than earlier)
        if len(matches) >= 4:
            recent_deaths = sum(m.get("deaths", 0) for m in matches[:3]) / 3.0
            earlier_deaths = sum(m.get("deaths", 0) for m in matches[3:]) / max(len(matches) - 3, 1)
            if earlier_deaths > 0 and recent_deaths / earlier_deaths > self._thresholds["death_spike_ratio"]:
                ratio = recent_deaths / earlier_deaths
                weight = min((ratio - 1.0) / 2.0, 1.0) * 0.25
                tilt_score += weight
                factors.append({"factor": "death_spike", "ratio": round(ratio, 2),
                                "recent_avg": round(recent_deaths, 1), "weight": round(weight, 3)})

        # Factor 3: Champion hopping (many different champions recently)
        recent_champs = set()
        for m in matches[:5]:
            cid = m.get("championId", m.get("champion_id", 0))
            if cid:
                recent_champs.add(cid)
        if len(recent_champs) >= self._thresholds["champion_hop_min"]:
            weight = min(len(recent_champs) / 5.0, 1.0) * 0.15
            tilt_score += weight
            factors.append({"factor": "champion_hopping", "unique_champs": len(recent_champs),
                            "weight": round(weight, 3)})

        # Factor 4: Short games (potential rage-quits or FF votes)
        short_games = sum(1 for m in matches[:5]
                          if m.get("game_duration", m.get("gameDuration", 9999)) < 900)
        if short_games >= self._thresholds["short_game_min"]:
            weight = min(short_games / 3.0, 1.0) * 0.15
            tilt_score += weight
            factors.append({"factor": "short_games", "count": short_games,
                            "weight": round(weight, 3)})

        # Factor 5: High death games (rage-playing)
        rage_games = sum(1 for m in matches[:5]
                         if m.get("deaths", 0) >= self._thresholds["rage_death_threshold"])
        if rage_games >= 2:
            weight = min(rage_games / 3.0, 1.0) * 0.15
            tilt_score += weight
            factors.append({"factor": "rage_deaths", "games_with_8plus_deaths": rage_games,
                            "weight": round(weight, 3)})

        tilt_prob = round(min(max(tilt_score, 0.0), 1.0), 4)
        state = "tilted" if tilt_prob > 0.6 else "frustrated" if tilt_prob > 0.3 else "stable"

        recommendation = "play_normally"
        if state == "tilted":
            recommendation = "exploit_aggression"
        elif state == "frustrated":
            recommendation = "apply_pressure"

        result = {
            "puuid": puuid, "name": summoner_name,
            "tilt_probability": tilt_prob, "tilt_state": state,
            "factors": factors, "recommendation": recommendation,
            "matches_analyzed": len(matches),
        }
        if tilt_prob > 0.3:
            self._fire("tilt_detected", {"puuid": puuid, "prob": tilt_prob, "state": state})
        return result

    def detect_batch(self, opponents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect tilt for all opponents.

        Args:
            opponents: List of {puuid, summoner_name, recent_matches}.
        """
        self._op_count += 1
        results = []
        for opp in opponents:
            r = self.detect(opp.get("puuid", ""), opp.get("recent_matches", []),
                           opp.get("summoner_name", ""))
            results.append(r)
        results.sort(key=lambda x: x.get("tilt_probability", 0), reverse=True)
        most_tilted = results[0] if results else None
        return {"status": "ok", "results": results, "most_tilted": most_tilted}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "detect_count": self._detect_count}
