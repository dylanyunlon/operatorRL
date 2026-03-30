"""
BanPickIntelligence — Track ban/pick patterns, synergies, and recommend draft strategy.

Architecture (拿来主义):
  查看 **draft_phase_intelligence.py** 的BP阶段逻辑。
  实现 **BanPickIntelligence**。

Location: integrations/lol-history/src/lol_history/ban_pick_intelligence.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.ban_pick_intelligence.v1"


class BanPickIntelligence:
    """Track draft patterns and recommend bans/picks."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._drafts: List[Dict[str, Any]] = []
        self._ban_freq: Dict[str, int] = defaultdict(int)
        self._pick_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0})
        self._synergy: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0})

    def record_draft(self, bans: List[str], picks: List[str], won: bool) -> None:
        self._drafts.append({"bans": bans, "picks": picks, "won": won})
        for b in bans:
            self._ban_freq[b] += 1
        for p in picks:
            self._pick_stats[p]["games"] += 1
            if won:
                self._pick_stats[p]["wins"] += 1
        # Record synergy pairs
        for i in range(len(picks)):
            for j in range(i + 1, len(picks)):
                pair = tuple(sorted([picks[i], picks[j]]))
                self._synergy[pair]["games"] += 1
                if won:
                    self._synergy[pair]["wins"] += 1

    def get_stats(self) -> Dict[str, Any]:
        return {"total_drafts": len(self._drafts)}

    def get_ban_frequency(self) -> Dict[str, int]:
        return dict(self._ban_freq)

    def get_pick_winrate(self, champion: str) -> float:
        s = self._pick_stats.get(champion, {"games": 0, "wins": 0})
        return s["wins"] / s["games"] if s["games"] else 0.0

    def recommend_bans(self, n: int = 5) -> List[str]:
        # Ban champions that beat us most often
        loss_against: Dict[str, int] = defaultdict(int)
        for d in self._drafts:
            if not d["won"]:
                for p in d.get("picks", []):
                    loss_against[p] += 1
        sorted_threats = sorted(loss_against.items(), key=lambda x: x[1], reverse=True)
        return [c for c, _ in sorted_threats[:n]]

    def get_pick_synergy(self, champ_a: str, champ_b: str) -> Dict[str, Any]:
        pair = tuple(sorted([champ_a, champ_b]))
        s = self._synergy.get(pair, {"games": 0, "wins": 0})
        return {
            "champions": list(pair),
            "games": s["games"],
            "winrate": s["wins"] / s["games"] if s["games"] else 0.0,
        }

    def suggest_counter_bans(self, enemy_picks: List[str]) -> List[str]:
        return [p for p in enemy_picks if self.get_pick_winrate(p) < 0.45]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_drafts": len(self._drafts),
            "ban_frequency": dict(self._ban_freq),
            "pick_stats": {k: dict(v) for k, v in self._pick_stats.items()},
        }
