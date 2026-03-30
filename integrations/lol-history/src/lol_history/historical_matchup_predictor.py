"""
HistoricalMatchupPredictor — Predict matchup outcomes from historical data.

Architecture (拿来主义):
  查看 **matchup_database.py** + **combat_outcome_predictor.py** 的对位建模方式。
  实现 **HistoricalMatchupPredictor**，支持按role过滤、team预测和hardest matchup查询。

Location: integrations/lol-history/src/lol_history/historical_matchup_predictor.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.historical_matchup_predictor.v1"


def _confidence(n: int, max_n: int = 20) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class HistoricalMatchupPredictor:
    """Predict matchup outcomes from personal historical data."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        # (champion, opponent, role|None) -> {games, wins}
        self._data: Dict[Tuple[str, str, Optional[str]], Dict[str, int]] = defaultdict(
            lambda: {"games": 0, "wins": 0}
        )

    def record_matchup(self, champion: str, opponent: str, won: bool,
                       role: Optional[str] = None) -> None:
        self._data[(champion, opponent, role)]["games"] += 1
        if won:
            self._data[(champion, opponent, role)]["wins"] += 1
        # Also record without role filter
        if role is not None:
            self._data[(champion, opponent, None)]["games"] += 1
            if won:
                self._data[(champion, opponent, None)]["wins"] += 1

    def record_batch(self, records: List[Dict[str, Any]]) -> None:
        for r in records:
            self.record_matchup(r["champion"], r["opponent"], r["won"], r.get("role"))

    def predict_matchup(self, champion: str, opponent: str,
                        role: Optional[str] = None) -> Dict[str, Any]:
        s = self._data.get((champion, opponent, role), {"games": 0, "wins": 0})
        if s["games"] == 0:
            return {"predicted_winrate": 0.5, "confidence": 0.0, "games": 0}
        return {
            "predicted_winrate": s["wins"] / s["games"],
            "confidence": _confidence(s["games"]),
            "games": s["games"],
        }

    def predict_team_matchup(
        self, my_team: List[str], enemy_team: List[str],
        roles: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        matchup_preds = []
        for my_champ in my_team:
            for en_champ in enemy_team:
                role = roles.get(my_champ) if roles else None
                pred = self.predict_matchup(my_champ, en_champ, role)
                matchup_preds.append(pred)
        if not matchup_preds:
            return {"team_predicted_winrate": 0.5, "matchups": []}
        avg_wr = sum(p["predicted_winrate"] for p in matchup_preds) / len(matchup_preds)
        return {"team_predicted_winrate": avg_wr, "matchups": matchup_preds}

    def get_hardest_matchups(self, champion: str, n: int = 5) -> List[Dict[str, Any]]:
        results = []
        for (c, o, r), s in self._data.items():
            if c == champion and r is None and s["games"] > 0:
                results.append({
                    "opponent": o, "games": s["games"],
                    "winrate": s["wins"] / s["games"],
                })
        results.sort(key=lambda x: x["winrate"])
        return results[:n]

    def to_dict(self) -> Dict[str, Any]:
        return {"matchup_count": len(self._data)}
