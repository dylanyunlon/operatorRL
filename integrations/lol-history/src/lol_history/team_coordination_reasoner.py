"""
TeamCoordinationReasoner — Reasons about optimal team coordination actions.

Architecture (拿来主义):
  DI-star/distar/agent/default/model/module_utils.py — Attention multi-head coordination
  ELF/elf_python/zmq_adapter.py — multi-node coordination

Location: integrations/lol-history/src/lol_history/team_coordination_reasoner.py
"""
from __future__ import annotations
import logging, math, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.team_coordination_reasoner.v1"

def _distance(a, b): return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)

class TeamCoordinationReasoner:
    """Reasons about team coordination: engage/disengage/split/regroup.

    Public API: assess_teamfight, recommend_rally_point, evaluate_engage, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._assess_count = 0
        self._history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def assess_teamfight(self, allies: List[Dict], enemies: List[Dict]) -> Dict[str, Any]:
        self._op_count += 1
        self._assess_count += 1
        ally_hp = sum(a.get("health", 0) for a in allies)
        enemy_hp = sum(e.get("health", 0) for e in enemies)
        ally_count = len([a for a in allies if a.get("alive", True)])
        enemy_count = len([e for e in enemies if e.get("alive", True)])
        hp_ratio = ally_hp / max(enemy_hp, 1)
        number_advantage = ally_count - enemy_count
        if hp_ratio > 1.3 and number_advantage >= 0: recommendation = "engage"
        elif hp_ratio < 0.6 or number_advantage <= -2: recommendation = "disengage"
        elif number_advantage >= 2: recommendation = "engage"
        else: recommendation = "poke"
        entry = {"recommendation": recommendation, "hp_ratio": round(hp_ratio, 2),
                 "number_advantage": number_advantage, "timestamp": time.time()}
        self._history.append(entry)
        self._fire("teamfight_assessed", entry)
        return {"status": "ok", **entry}

    def recommend_rally_point(self, ally_positions: List[tuple]) -> Dict[str, Any]:
        self._op_count += 1
        if not ally_positions: return {"status": "ok", "rally_point": (0, 0)}
        cx = sum(p[0] for p in ally_positions) / len(ally_positions)
        cy = sum(p[1] for p in ally_positions) / len(ally_positions)
        return {"status": "ok", "rally_point": (round(cx, 1), round(cy, 1)), "allies": len(ally_positions)}

    def evaluate_engage(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        if context is None: context = {}
        ult_ready = context.get("ult_ready_count", 0)
        ally_hp_pct = context.get("avg_ally_hp_pct", 0.5)
        score = ult_ready * 0.2 + ally_hp_pct * 0.5 + (1 if context.get("number_advantage", 0) > 0 else 0) * 0.3
        return {"status": "ok", "engage_score": round(score, 3), "should_engage": score > 0.6}

    def get_stats(self) -> Dict[str, Any]:
        rec_dist = {}
        for e in self._history: rec_dist[e.get("recommendation", "?")] = rec_dist.get(e.get("recommendation", "?"), 0) + 1
        return {"assessments": self._assess_count, "recommendation_distribution": rec_dist, "total_ops": self._op_count}

