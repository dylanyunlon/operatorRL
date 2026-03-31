"""
ItemBuildPathIntelligence — Predicts and advises item builds from historical data.

Architecture (拿来主义):
  Seraphine/app/lol/opgg.py — OpggDataParser build recommendation data
  Seraphine/app/lol/connector.py — JsonManager.getItemIconPath item database
  Seraphine/app/lol/tools.py — participant items extraction from parseGames

Location: integrations/lol-history/src/lol_history/item_build_path_intelligence.py

Design Notes (Knuth-level critique):
  User:
    - Tracks opponent's preferred build paths per champion from history.
    - Detects build deviations in live game: "enemy ADC skipped IE, building lethality."
  System:
    - Build path is ordered sequence of completed items (components excluded).
    - Frequency-based prediction; no ML needed for item order prediction.
"""
from __future__ import annotations
import logging, time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.item_build_path_intelligence.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class ItemBuildPathIntelligence:
    """Predicts and advises item builds from historical match data.

    Public API: record_build, predict_build, detect_deviation,
                get_champion_builds, suggest_counter_items, get_stats
    """
    def __init__(self, max_history_per_champ: int = 200) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._predict_count = 0
        self._max_history = max_history_per_champ
        # champion_id → list of build records
        self._build_history: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        # champion_id → Counter of item sequences (as tuples)
        self._build_frequency: Dict[int, Counter] = defaultdict(Counter)

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _extract_items(self, participant: Dict[str, Any]) -> List[int]:
        """Extract completed items from participant data, filtering zeroes and wards."""
        items = []
        for i in range(7):
            item_id = participant.get(f"item{i}", 0)
            if item_id and item_id > 0 and item_id not in (3340, 3363, 3364, 2055):
                items.append(item_id)
        return items

    def record_build(self, champion_id: int, items: List[int],
                      win: bool = False, role: str = "",
                      game_duration: int = 0) -> Dict[str, Any]:
        """Record a build observation from match history."""
        self._op_count += 1
        record = {
            "champion_id": champion_id, "items": items, "win": win,
            "role": role, "game_duration": game_duration,
            "timestamp": time.time(),
        }
        history = self._build_history[champion_id]
        history.append(record)
        if len(history) > self._max_history:
            self._build_history[champion_id] = history[-self._max_history:]
        # Track build frequency (first 3 items as signature)
        if len(items) >= 3:
            sig = tuple(items[:3])
            self._build_frequency[champion_id][sig] += 1
        return {"status": "ok", "champion_id": champion_id,
                "items_recorded": len(items), "total_builds": len(history)}

    def record_from_matches(self, matches: List[Dict[str, Any]],
                             target_puuid: str = "") -> Dict[str, Any]:
        """Bulk record builds from match list."""
        self._op_count += 1
        recorded = 0
        for match in matches:
            info = match.get("info", match)
            duration = info.get("gameDuration", 0)
            for p in info.get("participants", []):
                if target_puuid and p.get("puuid", "") != target_puuid:
                    continue
                champ_id = p.get("championId", 0)
                items = self._extract_items(p)
                role = p.get("teamPosition", p.get("lane", ""))
                win = p.get("win", False)
                if champ_id and items:
                    self.record_build(champ_id, items, win, role, duration)
                    recorded += 1
        return {"status": "ok", "recorded": recorded}

    def predict_build(self, champion_id: int, current_items: List[int] = None
                       ) -> Dict[str, Any]:
        """Predict likely build path for a champion."""
        self._op_count += 1
        self._predict_count += 1
        current_items = current_items or []
        counter = self._build_frequency.get(champion_id, Counter())
        if not counter:
            return {"status": "ok", "champion_id": champion_id,
                    "predictions": [], "confidence": 0.0}
        most_common = counter.most_common(5)
        total = sum(counter.values())
        predictions = []
        for build_sig, count in most_common:
            prob = round(_safe_div(count, total), 3)
            # Check if current items match start of this build
            match_depth = 0
            for i, item in enumerate(current_items):
                if i < len(build_sig) and build_sig[i] == item:
                    match_depth += 1
                else:
                    break
            predictions.append({
                "build_signature": list(build_sig),
                "probability": prob,
                "observations": count,
                "current_match_depth": match_depth,
            })
        # Sort by match depth (prioritize builds that match current items)
        predictions.sort(key=lambda p: (p["current_match_depth"], p["probability"]),
                          reverse=True)
        top = predictions[0] if predictions else {}
        confidence = top.get("probability", 0.0)
        return {"status": "ok", "champion_id": champion_id,
                "predictions": predictions, "confidence": confidence,
                "total_observations": total}

    def detect_deviation(self, champion_id: int,
                          current_items: List[int]) -> Dict[str, Any]:
        """Detect if current build deviates from expected patterns."""
        self._op_count += 1
        prediction = self.predict_build(champion_id, current_items)
        preds = prediction.get("predictions", [])
        if not preds or not current_items:
            return {"status": "ok", "deviation": False, "reason": "insufficient_data"}
        top_build = preds[0].get("build_signature", [])
        match_depth = preds[0].get("current_match_depth", 0)
        deviation = match_depth < len(current_items) * 0.5
        reason = ""
        if deviation:
            expected_set = set(top_build)
            actual_set = set(current_items)
            unexpected = actual_set - expected_set
            reason = f"unexpected_items: {list(unexpected)}" if unexpected else "order_deviation"
        self._fire("deviation_check", {"champion_id": champion_id, "deviated": deviation})
        return {"status": "ok", "deviation": deviation, "reason": reason,
                "match_depth": match_depth, "expected_build": top_build}

    def get_champion_builds(self, champion_id: int, n: int = 5) -> Dict[str, Any]:
        """Get top N builds for a champion with win rates."""
        self._op_count += 1
        history = self._build_history.get(champion_id, [])
        if not history:
            return {"status": "ok", "champion_id": champion_id, "builds": []}
        counter = self._build_frequency.get(champion_id, Counter())
        top_builds = []
        for sig, count in counter.most_common(n):
            # Compute win rate for this build
            matching = [b for b in history if tuple(b["items"][:3]) == sig]
            wins = sum(1 for b in matching if b.get("win", False))
            wr = round(_safe_div(wins, len(matching)) * 100, 1)
            top_builds.append({
                "items": list(sig), "count": count,
                "winrate": wr, "wins": wins, "losses": len(matching) - wins,
            })
        return {"status": "ok", "champion_id": champion_id, "builds": top_builds}

    def suggest_counter_items(self, enemy_champion_id: int,
                               my_champion_id: int) -> Dict[str, Any]:
        """Suggest items based on enemy build tendencies."""
        self._op_count += 1
        enemy_pred = self.predict_build(enemy_champion_id)
        preds = enemy_pred.get("predictions", [])
        if not preds:
            return {"status": "ok", "suggestions": [], "reason": "no_enemy_data"}
        top_enemy_build = preds[0].get("build_signature", [])
        # Simple counter logic placeholder — in production, would reference item database
        suggestions = []
        if top_enemy_build:
            suggestions.append({
                "reason": f"enemy_prefers_{len(top_enemy_build)}_items",
                "confidence": preds[0].get("probability", 0),
            })
        return {"status": "ok", "enemy_champion": enemy_champion_id,
                "enemy_likely_build": top_enemy_build, "suggestions": suggestions}

    def get_stats(self) -> Dict[str, Any]:
        total_builds = sum(len(v) for v in self._build_history.values())
        return {"predict_count": self._predict_count, "total_builds": total_builds,
                "champions_tracked": len(self._build_history),
                "total_ops": self._op_count}
