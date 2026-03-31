"""
OpponentAdaptationTracker — Tracks opponent adaptation within and across games.

Architecture (拿来主义):
  opponent_behavior_modeler.py — behavior modeling
  strategy_drift_detector.py — drift detection patterns

Location: integrations/lol-history/src/lol_history/opponent_adaptation_tracker.py

Design Notes (Knuth-level critique):
  User:
    - track() records opponent state changes; get_adaptation() shows how they adapted.
    - Detects: build path changes, role swaps, strategy shifts between games.
  System:
    - Within-game adaptation: compares early vs late behavior.
    - Cross-game adaptation: compares current game patterns to historical baseline.
"""
from __future__ import annotations
import logging, time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.opponent_adaptation_tracker.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class OpponentAdaptationTracker:
    """Tracks opponent adaptation within and across games.

    Public API: set_historical_profile, track, get_adaptation, get_cross_game_adaptation, get_stats
    """
    def __init__(self, window_size: int = 100) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._historical_profiles: Dict[str, Dict[str, Any]] = {}
        self._live_observations: Dict[str, deque] = {}
        self._adaptations_detected: List[Dict] = []
        self._window_size = window_size
        self._track_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_historical_profile(self, puuid: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Set historical profile for cross-game adaptation detection.

        Args:
            profile: Dict with preferred_playstyle, typical_build_order, avg_aggression, etc.
        """
        self._op_count += 1
        self._historical_profiles[puuid] = profile
        return {"status": "ok", "puuid": puuid}

    def track(self, puuid: str, observation: Dict[str, Any]) -> Dict[str, Any]:
        """Record a live observation of opponent behavior.

        Args:
            observation: {game_time, aggression_score, positioning, items_purchased,
                          action_type, context, ...}
        """
        self._op_count += 1
        self._track_count += 1
        self._live_observations.setdefault(puuid, deque(maxlen=self._window_size))
        observation["ts"] = time.time()
        self._live_observations[puuid].append(observation)
        return {"status": "ok", "observations": len(self._live_observations[puuid])}

    def get_adaptation(self, puuid: str) -> Dict[str, Any]:
        """Detect within-game adaptation for an opponent.

        Compares early-game behavior to recent behavior.
        """
        self._op_count += 1
        obs = list(self._live_observations.get(puuid, []))
        if len(obs) < 6:
            return {"status": "ok", "puuid": puuid, "adaptations": [],
                    "note": "insufficient_observations"}

        # Split into early and recent
        split_point = len(obs) // 2
        early = obs[:split_point]
        recent = obs[split_point:]

        adaptations = []

        # Compare aggression
        early_agg = [o.get("aggression_score", 0.5) for o in early if "aggression_score" in o]
        recent_agg = [o.get("aggression_score", 0.5) for o in recent if "aggression_score" in o]
        if early_agg and recent_agg:
            early_avg = sum(early_agg) / len(early_agg)
            recent_avg = sum(recent_agg) / len(recent_agg)
            delta = recent_avg - early_avg
            if abs(delta) > 0.15:
                direction = "more_aggressive" if delta > 0 else "more_passive"
                adaptations.append({
                    "metric": "aggression", "direction": direction,
                    "early_avg": round(early_avg, 3), "recent_avg": round(recent_avg, 3),
                    "delta": round(delta, 3), "significance": "high" if abs(delta) > 0.3 else "moderate",
                })

        # Compare positioning
        early_pos = [o.get("positioning", "") for o in early if o.get("positioning")]
        recent_pos = [o.get("positioning", "") for o in recent if o.get("positioning")]
        if early_pos and recent_pos:
            from collections import Counter
            early_mode = Counter(early_pos).most_common(1)[0][0]
            recent_mode = Counter(recent_pos).most_common(1)[0][0]
            if early_mode != recent_mode:
                adaptations.append({
                    "metric": "positioning", "direction": f"{early_mode}_to_{recent_mode}",
                    "early_mode": early_mode, "recent_mode": recent_mode,
                    "significance": "moderate",
                })

        # Compare action types
        early_actions = Counter(o.get("action_type", "") for o in early if o.get("action_type"))
        recent_actions = Counter(o.get("action_type", "") for o in recent if o.get("action_type"))
        for action in set(list(early_actions.keys()) + list(recent_actions.keys())):
            early_rate = _safe_div(early_actions.get(action, 0), len(early))
            recent_rate = _safe_div(recent_actions.get(action, 0), len(recent))
            if abs(recent_rate - early_rate) > 0.2:
                direction = "increased" if recent_rate > early_rate else "decreased"
                adaptations.append({
                    "metric": f"action_{action}", "direction": direction,
                    "early_rate": round(early_rate, 3), "recent_rate": round(recent_rate, 3),
                    "significance": "moderate",
                })

        if adaptations:
            self._adaptations_detected.extend(
                [{**a, "puuid": puuid, "ts": time.time()} for a in adaptations])
            self._fire("adaptation_detected", {"puuid": puuid, "count": len(adaptations)})

        return {"status": "ok", "puuid": puuid, "adaptations": adaptations,
                "observations_analyzed": len(obs)}

    def get_cross_game_adaptation(self, puuid: str,
                                   current_game_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Detect cross-game adaptation by comparing current game to historical profile."""
        self._op_count += 1
        hist = self._historical_profiles.get(puuid)
        if not hist:
            return {"status": "ok", "puuid": puuid, "cross_game": [],
                    "note": "no_historical_profile"}

        current = current_game_data or {}
        adaptations = []

        # Check if they changed champion from their comfort picks
        hist_mains = set(hist.get("main_champions", []))
        current_champ = current.get("champion_id", 0)
        if hist_mains and current_champ and current_champ not in hist_mains:
            adaptations.append({
                "metric": "champion_choice", "direction": "off_main",
                "historical_mains": list(hist_mains),
                "current_champion": current_champ,
                "significance": "high",
            })

        # Check playstyle shift
        hist_style = hist.get("preferred_playstyle", "")
        current_style = current.get("observed_playstyle", "")
        if hist_style and current_style and hist_style != current_style:
            adaptations.append({
                "metric": "playstyle", "direction": f"{hist_style}_to_{current_style}",
                "significance": "high",
            })

        return {"status": "ok", "puuid": puuid, "cross_game": adaptations}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "track_count": self._track_count,
                "tracked_opponents": len(self._live_observations),
                "historical_profiles": len(self._historical_profiles),
                "total_adaptations": len(self._adaptations_detected)}
