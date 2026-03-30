"""
SummonerSpellPatternAnalyzer — Track summoner spell combo preferences and winrates.

Architecture (拿来主义):
  查看 **champion_tendency_analyzer.py** 的偏好建模方式。
  实现 **SummonerSpellPatternAnalyzer**。

Location: integrations/lol-history/src/lol_history/summoner_spell_pattern_analyzer.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.summoner_spell_pattern_analyzer.v1"


class SummonerSpellPatternAnalyzer:
    """Analyze summoner spell selection patterns."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._records: List[Dict[str, Any]] = []
        self._combo_stats: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
            lambda: {"games": 0, "wins": 0}
        )
        self._champ_stats: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._role_stats: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def record_spells(self, spell1: str, spell2: str, champion: str,
                      role: str, won: bool) -> None:
        combo = tuple(sorted([spell1, spell2]))
        self._combo_stats[combo]["games"] += 1
        if won:
            self._combo_stats[combo]["wins"] += 1
        rec = {"spell1": spell1, "spell2": spell2, "champion": champion,
               "role": role, "won": won}
        self._records.append(rec)
        self._champ_stats[champion].append(rec)
        self._role_stats[role].append(rec)

    def get_spell_stats(self) -> Dict[str, Any]:
        return {"total_games": len(self._records)}

    def get_most_common_combo(self) -> Dict[str, Any]:
        if not self._combo_stats:
            return {"spells": [], "games": 0}
        best = max(self._combo_stats.items(), key=lambda x: x[1]["games"])
        return {"spells": list(best[0]), "games": best[1]["games"],
                "winrate": best[1]["wins"] / best[1]["games"] if best[1]["games"] else 0.0}

    def get_combo_winrate(self, spell1: str, spell2: str) -> float:
        combo = tuple(sorted([spell1, spell2]))
        s = self._combo_stats.get(combo, {"games": 0, "wins": 0})
        return s["wins"] / s["games"] if s["games"] else 0.0

    def get_champion_spell_stats(self, champion: str) -> Dict[str, Any]:
        recs = self._champ_stats.get(champion, [])
        return {"champion": champion, "games": len(recs)}

    def get_role_spell_stats(self, role: str) -> Dict[str, Any]:
        recs = self._role_stats.get(role, [])
        return {"role": role, "games": len(recs)}

    def recommend_spells(self, champion: str, role: str) -> Dict[str, Any]:
        recs = [r for r in self._records if r["champion"] == champion and r["role"] == role]
        if not recs:
            return {"spells": ["Flash", "Ignite"], "confidence": 0.0}
        combo_counts: Dict[Tuple, int] = defaultdict(int)
        for r in recs:
            combo_counts[tuple(sorted([r["spell1"], r["spell2"]]))] += 1
        best = max(combo_counts.items(), key=lambda x: x[1])
        return {"spells": list(best[0]), "games": best[1], "confidence": min(1.0, best[1] / 10)}

    def to_dict(self) -> Dict[str, Any]:
        return {"total_games": len(self._records),
                "combos": {f"{k[0]}+{k[1]}": v for k, v in self._combo_stats.items()}}
