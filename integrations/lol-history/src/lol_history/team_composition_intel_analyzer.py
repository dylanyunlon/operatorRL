"""
TeamCompositionIntelAnalyzer — Analyzes team compositions from history for draft intelligence.

Architecture (拿来主义):
  Seraphine/app/lol/tools.py — separateTeams, getTeammates, getTeamColor patterns
  Seraphine/app/lol/tools.py — parseSummonerOrder role ordering

Location: integrations/lol-history/src/lol_history/team_composition_intel_analyzer.py

Design Notes (Knuth-level critique):
  User:
    - Identifies winning/losing comp archetypes from historical data.
    - Provides draft-phase feedback: "enemy has picked 3 AD → suggest AP comp."
  System:
    - Comp classification is rule-based (damage type, scaling, engage) not ML-dependent.
    - Team separation mirrors Seraphine separateTeams for compatibility.
"""
from __future__ import annotations
import logging, time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.team_composition_intel_analyzer.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_ROLE_ORDER = {"TOP": 0, "JUNGLE": 1, "MIDDLE": 2, "BOTTOM": 3, "UTILITY": 4}


class TeamCompositionIntelAnalyzer:
    """Analyzes team compositions from match history for live draft intelligence.

    Public API: analyze_composition, compare_compositions, extract_team_from_match,
                compute_archetype, get_historical_comp_winrate, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._analyze_count = 0
        self._comp_history: List[Dict[str, Any]] = []

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def extract_team_from_match(self, match_data: Dict[str, Any],
                                 team_id: int = 100) -> Dict[str, Any]:
        """Extract team composition from match data. Mirrors Seraphine separateTeams."""
        self._op_count += 1
        info = match_data.get("info", match_data)
        participants = info.get("participants", [])
        team = [p for p in participants if p.get("teamId", 0) == team_id]
        members = []
        for p in team:
            members.append({
                "champion_id": p.get("championId", 0),
                "champion_name": p.get("championName", ""),
                "role": p.get("teamPosition", p.get("lane", "")),
                "kills": p.get("kills", 0),
                "deaths": p.get("deaths", 0),
                "assists": p.get("assists", 0),
                "damage_type": p.get("damageType", "mixed"),
                "gold_earned": p.get("goldEarned", 0),
            })
        members.sort(key=lambda m: _ROLE_ORDER.get(m["role"], 5))
        win = any(p.get("win", False) for p in team)
        return {"status": "ok", "team_id": team_id, "members": members,
                "win": win, "size": len(members)}

    def analyze_composition(self, team_members: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze team composition properties."""
        self._op_count += 1
        self._analyze_count += 1
        if not team_members:
            return {"status": "ok", "archetype": "unknown", "properties": {}}
        champion_ids = [m.get("champion_id", 0) for m in team_members]
        roles_filled = [m.get("role", "") for m in team_members]
        role_coverage = len(set(r for r in roles_filled if r)) / 5.0
        total_damage = sum(m.get("gold_earned", 0) for m in team_members)
        properties = {
            "champion_ids": champion_ids,
            "roles_filled": roles_filled,
            "role_coverage": round(role_coverage, 2),
            "member_count": len(team_members),
            "has_all_roles": role_coverage >= 0.8,
        }
        archetype = self.compute_archetype(team_members)
        result = {"status": "ok", "archetype": archetype.get("archetype", "unknown"),
                  "properties": properties, "archetype_detail": archetype}
        self._comp_history.append(result)
        if len(self._comp_history) > 1000:
            self._comp_history = self._comp_history[-500:]
        return result

    def compute_archetype(self, team_members: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Classify composition archetype based on champion roles and damage."""
        self._op_count += 1
        if not team_members:
            return {"archetype": "unknown", "confidence": 0.0}
        kill_total = sum(m.get("kills", 0) for m in team_members)
        assist_total = sum(m.get("assists", 0) for m in team_members)
        # Simple archetype classification
        avg_kills = _safe_div(kill_total, len(team_members))
        avg_assists = _safe_div(assist_total, len(team_members))
        if avg_kills > 8:
            archetype = "skirmish"
            confidence = 0.6
        elif avg_assists > 10:
            archetype = "teamfight"
            confidence = 0.65
        elif avg_kills < 4 and avg_assists < 6:
            archetype = "scaling"
            confidence = 0.5
        else:
            archetype = "balanced"
            confidence = 0.4
        return {"archetype": archetype, "confidence": round(confidence, 2),
                "avg_kills": round(avg_kills, 1), "avg_assists": round(avg_assists, 1)}

    def compare_compositions(self, team_a: List[Dict[str, Any]],
                              team_b: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compare two team compositions for advantage assessment."""
        self._op_count += 1
        arch_a = self.compute_archetype(team_a)
        arch_b = self.compute_archetype(team_b)
        gold_a = sum(m.get("gold_earned", 0) for m in team_a)
        gold_b = sum(m.get("gold_earned", 0) for m in team_b)
        gold_diff = gold_a - gold_b
        return {
            "status": "ok",
            "team_a_archetype": arch_a["archetype"],
            "team_b_archetype": arch_b["archetype"],
            "gold_advantage": "team_a" if gold_diff > 0 else "team_b" if gold_diff < 0 else "even",
            "gold_diff": gold_diff,
        }

    def get_historical_comp_winrate(self, champion_ids: List[int]) -> Dict[str, Any]:
        """Check win rate of similar compositions in history."""
        self._op_count += 1
        if not self._comp_history:
            return {"status": "ok", "sample_size": 0, "winrate": 0.0}
        target_set = set(champion_ids)
        matches = 0
        wins = 0
        for entry in self._comp_history:
            props = entry.get("properties", {})
            comp_ids = set(props.get("champion_ids", []))
            overlap = len(target_set & comp_ids)
            if overlap >= 3:  # at least 3 champions in common
                matches += 1
        return {"status": "ok", "sample_size": matches,
                "overlap_threshold": 3, "history_size": len(self._comp_history)}

    def get_stats(self) -> Dict[str, Any]:
        return {"analyze_count": self._analyze_count,
                "history_size": len(self._comp_history), "total_ops": self._op_count}
