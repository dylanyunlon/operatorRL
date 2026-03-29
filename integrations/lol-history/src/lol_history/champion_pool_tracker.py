"""
Champion Pool Tracker — Track champion usage patterns and pool diversity.
Location: integrations/lol-history/src/lol_history/champion_pool_tracker.py
Reference: Seraphine/app/lol/tools.py: getRecentChampions
"""
from __future__ import annotations
import logging, math, time
from collections import Counter, defaultdict
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.champion_pool_tracker.v1"

class ChampionPoolTracker:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def get_champion_counts(self, games: list[dict[str, Any]]) -> dict[int, int]:
        counts: Counter = Counter()
        for g in games:
            cid = g.get("championId", 0)
            if cid:
                counts[cid] += 1
        return dict(counts)

    def get_top_champions(self, games: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
        counts = self.get_champion_counts(games)
        wr = self.get_champion_winrates(games)
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return [{"champion_id": cid, "games": cnt, "winrate": wr.get(cid, 0.0)} for cid, cnt in ranked[:n]]

    def get_champion_winrates(self, games: list[dict[str, Any]]) -> dict[int, float]:
        stats: dict[int, dict] = defaultdict(lambda: {"wins": 0, "total": 0})
        for g in games:
            cid = g.get("championId", 0)
            if not cid:
                continue
            stats[cid]["total"] += 1
            if g.get("stats", {}).get("win", False):
                stats[cid]["wins"] += 1
        return {cid: s["wins"] / s["total"] if s["total"] > 0 else 0.0 for cid, s in stats.items()}

    def pool_diversity_score(self, games: list[dict[str, Any]]) -> float:
        counts = self.get_champion_counts(games)
        total = sum(counts.values())
        if total == 0 or len(counts) <= 1:
            return 0.0
        entropy = -sum((c / total) * math.log2(c / total) for c in counts.values() if c > 0)
        max_entropy = math.log2(len(counts))
        if max_entropy == 0:
            return 0.0
        return min(entropy / max_entropy, 1.0)

    def is_one_trick(self, games: list[dict[str, Any]], threshold: float = 0.7) -> bool:
        counts = self.get_champion_counts(games)
        total = sum(counts.values())
        if total == 0:
            return False
        return (max(counts.values()) / total) >= threshold

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "champion_pool_tracker", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
