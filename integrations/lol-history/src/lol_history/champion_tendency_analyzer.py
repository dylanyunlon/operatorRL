"""
Champion Tendency Analyzer — Detect per-champion play patterns.

Location: integrations/lol-history/src/lol_history/champion_tendency_analyzer.py

Reference (拿来主义):
  - Seraphine: getGameDetailByGameId champion-level stats
  - leagueoflegends-optimizer: article5.md per-champion features
  - integrations/lol-history/src/lol_history/player_profiler.py: profiling pattern
"""
from __future__ import annotations
import logging, time, statistics
from typing import Any, Callable, Optional
from collections import Counter

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.champion_tendency_analyzer.v1"


class ChampionTendencyAnalyzer:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def analyze(self, champion: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
        filtered = [m for m in matches if m.get("champion") == champion]
        if not filtered:
            self._fire("analyze_empty", {"champion": champion})
            return {"champion": champion, "games": 0, "aggression_score": 0.0,
                    "farm_score": 0.0, "early_game_tendency": 0.0, "death_rate": 0.0,
                    "consistency_score": 0.0, "preferred_role": None}

        n = len(filtered)
        kill_rates, cs_rates, death_rates_list = [], [], []
        fb_count = 0
        roles: list[str] = []

        for m in filtered:
            dur = m.get("duration_minutes", 1) or 1
            k, d, a = m.get("kills", 0), m.get("deaths", 0), m.get("assists", 0)
            cs = m.get("cs", 0)
            kill_rates.append(k / dur)
            cs_rates.append(cs / dur)
            death_rates_list.append(d / dur)
            if m.get("first_blood"):
                fb_count += 1
            if m.get("role"):
                roles.append(m["role"])

        avg_kill_rate = sum(kill_rates) / n
        avg_cs_rate = sum(cs_rates) / n
        avg_death_rate = sum(death_rates_list) / n

        # Aggression: high kills/min + first blood frequency
        aggression_raw = min(avg_kill_rate / 0.5, 1.0) * 0.6 + (fb_count / n) * 0.4
        aggression_score = min(max(aggression_raw, 0.0), 1.0)

        # Farm: cs/min normalized (10 cs/min = 1.0)
        farm_score = min(avg_cs_rate / 10.0, 1.0)

        # Early game: first blood ratio
        early_game_tendency = fb_count / n

        # Death rate: average deaths per minute
        death_rate = avg_death_rate

        # Consistency: 1 - coefficient of variation of KDA
        kdas = [(m.get("kills", 0) + m.get("assists", 0)) / max(1, m.get("deaths", 1)) for m in filtered]
        if n >= 2 and statistics.mean(kdas) > 0:
            cv = statistics.stdev(kdas) / statistics.mean(kdas)
            consistency_score = max(0.0, min(1.0, 1.0 - cv))
        else:
            consistency_score = 0.5

        preferred_role = Counter(roles).most_common(1)[0][0] if roles else None

        self._fire("analyze_complete", {"champion": champion, "games": n})
        return {
            "champion": champion, "games": n,
            "aggression_score": aggression_score, "farm_score": farm_score,
            "early_game_tendency": early_game_tendency, "death_rate": death_rate,
            "consistency_score": consistency_score, "preferred_role": preferred_role,
        }

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
