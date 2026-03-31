"""
VisionControlReasoner — Reasons about optimal vision control actions.

Architecture (拿来主义):
  fiddler_lol_decoder.py — eventdata→ward event decoding
  dota2bot-OpenHyperAI/ — ward_purchase_cooldown vision management

Location: integrations/lol-history/src/lol_history/vision_control_reasoner.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.vision_control_reasoner.v1"

class VisionControlReasoner:
    """Reasons about ward placement, sweeping, and vision coverage.

    Public API: analyze_coverage, recommend_ward_spot, recommend_sweep, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._wards_placed: List[Dict] = []
        self._high_value_spots: List[Dict] = []
        self._analyze_count = 0
        # Default high-value vision spots (normalized coordinates)
        self._register_default_spots()

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _register_default_spots(self):
        self._high_value_spots = [
            {"name": "dragon_pit", "pos": (0.45, 0.35), "priority": "high"},
            {"name": "baron_pit", "pos": (0.55, 0.65), "priority": "high"},
            {"name": "river_mid", "pos": (0.5, 0.5), "priority": "medium"},
            {"name": "tri_bush_bot", "pos": (0.6, 0.25), "priority": "medium"},
            {"name": "tri_bush_top", "pos": (0.4, 0.75), "priority": "medium"},
        ]

    def analyze_coverage(self, active_wards: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._analyze_count += 1
        if active_wards is None: active_wards = []
        ward_positions = set()
        for w in active_wards:
            pos = w.get("pos", w.get("position"))
            if pos: ward_positions.add(tuple(pos) if isinstance(pos, list) else pos)
        covered_spots = 0
        uncovered = []
        for spot in self._high_value_spots:
            is_covered = any(abs(spot["pos"][0]-wp[0]) < 0.1 and abs(spot["pos"][1]-wp[1]) < 0.1 for wp in ward_positions) if ward_positions else False
            if is_covered: covered_spots += 1
            else: uncovered.append(spot)
        coverage = covered_spots / max(len(self._high_value_spots), 1)
        self._fire("coverage_analyzed", {"coverage": coverage})
        return {"status": "ok", "coverage": round(coverage, 3), "active_wards": len(active_wards),
                "uncovered_spots": uncovered[:3]}

    def recommend_ward_spot(self, game_phase: str = "mid_game", active_wards: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        analysis = self.analyze_coverage(active_wards)
        uncovered = analysis.get("uncovered_spots", [])
        priority_order = {"high": 0, "medium": 1, "low": 2}
        uncovered.sort(key=lambda s: priority_order.get(s.get("priority", "low"), 2))
        if uncovered:
            return {"status": "ok", "recommendation": uncovered[0], "reason": "highest priority uncovered spot"}
        return {"status": "ok", "recommendation": None, "reason": "all key spots covered"}

    def recommend_sweep(self, enemy_ward_estimates: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        if not enemy_ward_estimates: return {"status": "ok", "sweep_targets": [], "reason": "no estimated enemy wards"}
        sorted_targets = sorted(enemy_ward_estimates, key=lambda w: w.get("danger_level", 0), reverse=True)
        return {"status": "ok", "sweep_targets": sorted_targets[:3]}

    def get_stats(self) -> Dict[str, Any]:
        return {"analyses": self._analyze_count, "high_value_spots": len(self._high_value_spots), "total_ops": self._op_count}

