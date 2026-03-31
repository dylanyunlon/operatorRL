"""
TeamCompositionHistoryAnalyzer — Analyzes historical team composition performance.

Architecture (拿来主义):
  team_synergy_scorer.py — team synergy scoring patterns
  win_condition_analyzer.py — win condition identification

Location: integrations/lol-history/src/lol_history/team_composition_history_analyzer.py

Design Notes (Knuth-level critique):
  User:
    - analyze() scores team comp by historical win rate + synergy + win condition alignment.
    - Identifies the team comp archetype (teamfight/splitpush/pick/poke/siege).
  System:
    - Comp fingerprinting normalizes champion order for cache-friendly lookups.
    - Partial match (3/5 champions) broadens sample when exact match is rare.
"""
from __future__ import annotations
import logging, time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.team_composition_history_analyzer.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_ARCHETYPES = {
    "teamfight": {"aoe_cc", "engage", "wombo_combo"},
    "splitpush": {"duelist", "tower_pressure", "1v1"},
    "pick": {"burst", "catch", "single_target_cc"},
    "poke": {"long_range", "siege", "disengage"},
    "siege": {"tower_push", "zone_control", "waveclear"},
}


class TeamCompositionHistoryAnalyzer:
    """Analyzes team compositions using historical match data.

    Public API: ingest_comp_data, analyze, compare_comps, get_archetype, get_stats
    """
    def __init__(self, min_samples: int = 3) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._min_samples = min_samples
        # key: frozenset of champion_ids -> list of match results
        self._exact_comps: Dict[FrozenSet[int], List[Dict]] = defaultdict(list)
        # key: pair (champ_a, champ_b) -> {wins, total}
        self._pair_synergy: Dict[FrozenSet[int], Dict[str, int]] = defaultdict(lambda: {"wins": 0, "total": 0})
        self._champion_tags: Dict[int, List[str]] = {}
        self._analyze_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_champion_tags(self, tags: Dict[int, List[str]]) -> Dict[str, Any]:
        """Set champion archetype tags. {champion_id: [tag1, tag2, ...]}"""
        self._op_count += 1
        self._champion_tags = dict(tags)
        return {"status": "ok", "champions_tagged": len(self._champion_tags)}

    def ingest_comp_data(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ingest historical team composition records.

        Each record: {team_champions: [5 ids], win: bool, game_duration: int, ...}
        """
        self._op_count += 1
        ingested = 0
        for rec in records:
            champs = rec.get("team_champions", [])
            if len(champs) < 2:
                continue
            fs = frozenset(champs)
            self._exact_comps[fs].append(rec)
            # Update pair synergy
            champ_list = list(champs)
            for i in range(len(champ_list)):
                for j in range(i + 1, len(champ_list)):
                    pair = frozenset([champ_list[i], champ_list[j]])
                    self._pair_synergy[pair]["total"] += 1
                    if rec.get("win"):
                        self._pair_synergy[pair]["wins"] += 1
            ingested += 1
        return {"status": "ok", "ingested": ingested, "unique_comps": len(self._exact_comps)}

    def analyze(self, team_champions: List[int]) -> Dict[str, Any]:
        """Analyze a team composition.

        Returns:
            Dict with overall win rate, synergy scores, archetype, strengths/weaknesses.
        """
        self._op_count += 1
        self._analyze_count += 1
        fs = frozenset(team_champions)

        # Exact match
        exact_records = self._exact_comps.get(fs, [])
        exact_wr = None
        if len(exact_records) >= self._min_samples:
            wins = sum(1 for r in exact_records if r.get("win"))
            exact_wr = round(_safe_div(wins, len(exact_records)), 4)

        # Pair synergy scores
        pair_scores = []
        for i in range(len(team_champions)):
            for j in range(i + 1, len(team_champions)):
                pair = frozenset([team_champions[i], team_champions[j]])
                data = self._pair_synergy.get(pair)
                if data and data["total"] >= self._min_samples:
                    wr = _safe_div(data["wins"], data["total"])
                    pair_scores.append({
                        "pair": [team_champions[i], team_champions[j]],
                        "win_rate": round(wr, 4), "samples": data["total"],
                    })

        avg_pair_wr = (_safe_div(sum(p["win_rate"] for p in pair_scores), len(pair_scores))
                       if pair_scores else 0.5)

        # Archetype detection
        all_tags = []
        for champ_id in team_champions:
            all_tags.extend(self._champion_tags.get(champ_id, []))
        tag_counts = Counter(all_tags)

        archetype = "balanced"
        best_score = 0
        for arch_name, arch_tags in _ARCHETYPES.items():
            score = sum(tag_counts.get(t, 0) for t in arch_tags)
            if score > best_score:
                best_score = score
                archetype = arch_name

        # Strengths/weaknesses
        strengths = [t for t, c in tag_counts.most_common(3) if c >= 2]
        missing_tags = {"engage", "waveclear", "cc", "sustain", "tank"} - set(all_tags)
        weaknesses = list(missing_tags)[:3]

        result = {
            "status": "ok",
            "team_champions": team_champions,
            "exact_win_rate": exact_wr,
            "exact_samples": len(exact_records),
            "pair_synergy_avg": round(avg_pair_wr, 4),
            "pair_details": pair_scores,
            "archetype": archetype,
            "strengths": strengths,
            "weaknesses": weaknesses,
        }
        self._fire("analyzed", {"archetype": archetype, "exact_wr": exact_wr})
        return result

    def compare_comps(self, our_team: List[int], enemy_team: List[int]) -> Dict[str, Any]:
        """Compare two team compositions head-to-head."""
        self._op_count += 1
        our = self.analyze(our_team)
        enemy = self.analyze(enemy_team)

        our_score = (our.get("exact_win_rate") or our.get("pair_synergy_avg", 0.5))
        enemy_score = (enemy.get("exact_win_rate") or enemy.get("pair_synergy_avg", 0.5))

        return {
            "status": "ok",
            "our_analysis": our, "enemy_analysis": enemy,
            "advantage": "our_team" if our_score > enemy_score else "enemy_team" if enemy_score > our_score else "even",
            "score_delta": round(our_score - enemy_score, 4),
        }

    def get_archetype(self, team_champions: List[int]) -> Dict[str, Any]:
        """Quick archetype detection without full analysis."""
        self._op_count += 1
        all_tags = []
        for cid in team_champions:
            all_tags.extend(self._champion_tags.get(cid, []))
        tag_counts = Counter(all_tags)
        archetype = "balanced"
        best_score = 0
        for a_name, a_tags in _ARCHETYPES.items():
            s = sum(tag_counts.get(t, 0) for t in a_tags)
            if s > best_score:
                best_score = s
                archetype = a_name
        return {"status": "ok", "archetype": archetype, "tag_counts": dict(tag_counts)}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "analyze_count": self._analyze_count,
                "unique_comps": len(self._exact_comps),
                "pair_synergies": len(self._pair_synergy),
                "tagged_champions": len(self._champion_tags)}
