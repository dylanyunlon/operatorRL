"""
RecentChampionsProfiler — Profiles recent champion usage from match history.

Architecture (拿来主义):
  Seraphine/app/lol/tools.py — getRecentChampions: game list → champion frequency
  Seraphine/app/lol/champions.py — champion alias/id mapping

Location: integrations/lol-history/src/lol_history/recent_champions_profiler.py

Design Notes (Knuth-level critique):
  User:
    - Surfaces champion pool depth: "this player only plays 3 champions" vs "flex player."
    - Recency-weighted: last 5 games matter more than games from 2 weeks ago.
  System:
    - Champion frequency is O(n) single-pass over match list.
    - Decay window configurable to adapt to different analysis horizons.
"""
from __future__ import annotations
import logging, time, math
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.recent_champions_profiler.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class RecentChampionsProfiler:
    """Profiles recent champion usage and pool depth from match history.

    Public API: profile_from_matches, get_champion_frequency,
                compute_pool_depth, detect_one_trick, get_stats
    """
    def __init__(self, recency_decay: float = 0.95, max_games: int = 100) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._profile_count = 0
        self._max_games = max_games
        self._recency_decay = recency_decay

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def profile_from_matches(self, matches: List[Dict[str, Any]],
                              target_puuid: str = "") -> Dict[str, Any]:
        """Profile champion usage from match list. Mirrors getRecentChampions logic."""
        self._op_count += 1
        self._profile_count += 1
        champion_data: Dict[int, Dict[str, Any]] = {}
        total_games = 0
        for idx, match in enumerate(matches[:self._max_games]):
            weight = self._recency_decay ** idx
            info = match.get("info", match)
            participants = info.get("participants", [])
            for p in participants:
                puuid = p.get("puuid", "")
                if target_puuid and puuid != target_puuid:
                    continue
                champ_id = p.get("championId", 0)
                champ_name = p.get("championName", str(champ_id))
                win = p.get("win", p.get("stats", {}).get("win", False))
                kills = p.get("kills", p.get("stats", {}).get("kills", 0))
                deaths = p.get("deaths", p.get("stats", {}).get("deaths", 0))
                assists = p.get("assists", p.get("stats", {}).get("assists", 0))
                if champ_id not in champion_data:
                    champion_data[champ_id] = {
                        "champion_id": champ_id, "champion_name": champ_name,
                        "games": 0, "wins": 0, "weighted_games": 0.0,
                        "total_kills": 0, "total_deaths": 0, "total_assists": 0,
                    }
                cd = champion_data[champ_id]
                cd["games"] += 1
                cd["wins"] += 1 if win else 0
                cd["weighted_games"] += weight
                cd["total_kills"] += kills
                cd["total_deaths"] += deaths
                cd["total_assists"] += assists
                total_games += 1
        # Compute derived stats
        for cd in champion_data.values():
            g = cd["games"]
            cd["winrate"] = round(_safe_div(cd["wins"], g) * 100, 1)
            cd["avg_kda"] = round(_safe_div(cd["total_kills"] + cd["total_assists"],
                                            max(cd["total_deaths"], 1)), 2)
            cd["play_rate"] = round(_safe_div(g, total_games) * 100, 1) if total_games else 0.0
        # Sort by weighted games (recency-aware)
        sorted_champs = sorted(champion_data.values(),
                                key=lambda c: c["weighted_games"], reverse=True)
        self._fire("profiled", {"champions": len(sorted_champs), "games": total_games})
        return {"status": "ok", "champions": sorted_champs,
                "total_games": total_games, "unique_champions": len(sorted_champs)}

    def get_champion_frequency(self, matches: List[Dict[str, Any]],
                                target_puuid: str = "") -> Dict[str, Any]:
        """Simple champion frequency count. Direct mirror of Seraphine getRecentChampions."""
        self._op_count += 1
        counter: Counter = Counter()
        for match in matches[:self._max_games]:
            info = match.get("info", match)
            for p in info.get("participants", []):
                if target_puuid and p.get("puuid", "") != target_puuid:
                    continue
                counter[p.get("championId", 0)] += 1
        return {"status": "ok", "frequency": dict(counter.most_common()),
                "unique": len(counter), "total": sum(counter.values())}

    def compute_pool_depth(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Compute champion pool depth metrics from a profile."""
        self._op_count += 1
        champions = profile.get("champions", [])
        if not champions:
            return {"status": "ok", "depth": 0, "classification": "unknown"}
        total = sum(c.get("games", 0) for c in champions)
        # Shannon entropy for pool diversity
        entropy = 0.0
        for c in champions:
            p = _safe_div(c.get("games", 0), total)
            if p > 0:
                entropy -= p * math.log2(p)
        max_entropy = math.log2(len(champions)) if len(champions) > 1 else 1.0
        diversity = round(_safe_div(entropy, max_entropy), 3)
        # Classification
        if len(champions) <= 2:
            classification = "one_trick"
        elif diversity < 0.4:
            classification = "specialist"
        elif diversity < 0.7:
            classification = "moderate"
        else:
            classification = "flex_player"
        return {"status": "ok", "depth": len(champions), "entropy": round(entropy, 3),
                "diversity": diversity, "classification": classification}

    def detect_one_trick(self, profile: Dict[str, Any],
                          threshold: float = 0.5) -> Dict[str, Any]:
        """Detect if a player is a one-trick pony."""
        self._op_count += 1
        champions = profile.get("champions", [])
        total = profile.get("total_games", sum(c.get("games", 0) for c in champions))
        if not champions or total == 0:
            return {"status": "ok", "is_one_trick": False, "main_champion": None}
        top = champions[0]
        play_rate = _safe_div(top.get("games", 0), total)
        is_otp = play_rate >= threshold
        return {"status": "ok", "is_one_trick": is_otp,
                "main_champion": top.get("champion_name", ""),
                "main_play_rate": round(play_rate * 100, 1),
                "threshold": threshold}

    def get_stats(self) -> Dict[str, Any]:
        return {"profile_count": self._profile_count, "total_ops": self._op_count}
