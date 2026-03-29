"""
Team Synergy Scorer — Evaluate historical duo/trio synergy.
Location: integrations/lol-history/src/lol_history/team_synergy_scorer.py
Reference: Seraphine match history, leagueoflegends-optimizer team features
"""
from __future__ import annotations
import logging, time
from itertools import combinations
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.team_synergy_scorer.v1"

class TeamSynergyScorer:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def score(self, team: list[str], matches: list[dict[str, Any]]) -> dict[str, Any]:
        if len(team) < 2 or not matches:
            self._fire("score_empty", {})
            return {"synergy_score": 0.0, "duo_stats": {}, "trio_stats": {}}

        duo_stats: dict[tuple[str, str], dict[str, Any]] = {}
        trio_stats: dict[tuple[str, str, str], dict[str, Any]] = {}

        # Duo combinations
        for pair in combinations(sorted(team), 2):
            games, wins = 0, 0
            for m in matches:
                players = set(m.get("players", []))
                if pair[0] in players and pair[1] in players:
                    games += 1
                    if m.get("win"):
                        wins += 1
            if games > 0:
                duo_stats[pair] = {"games_together": games, "wins": wins, "winrate": wins / games}

        # Trio combinations (if 3+ players)
        if len(team) >= 3:
            for trio in combinations(sorted(team), 3):
                games, wins = 0, 0
                for m in matches:
                    players = set(m.get("players", []))
                    if all(p in players for p in trio):
                        games += 1
                        if m.get("win"):
                            wins += 1
                if games > 0:
                    trio_stats[trio] = {"games_together": games, "wins": wins, "winrate": wins / games}

        # Overall synergy score: weighted average of duo winrates
        if duo_stats:
            total_games = sum(d["games_together"] for d in duo_stats.values())
            weighted_wr = sum(d["winrate"] * d["games_together"] for d in duo_stats.values())
            synergy_score = min(1.0, weighted_wr / total_games if total_games > 0 else 0.0)
        else:
            synergy_score = 0.0

        self._fire("score_complete", {"synergy_score": synergy_score, "duos": len(duo_stats)})
        return {"synergy_score": synergy_score, "duo_stats": duo_stats, "trio_stats": trio_stats}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
