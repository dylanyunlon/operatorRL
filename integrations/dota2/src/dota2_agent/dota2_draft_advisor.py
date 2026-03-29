"""
Dota2 Draft Advisor — History+meta based hero recommendation.

Location: integrations/dota2/src/dota2_agent/dota2_draft_advisor.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.dota2.dota2_draft_advisor.v1"

class Dota2DraftAdvisor:
    """Advise on hero picks and bans for Dota2 drafting."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def suggest_picks(self, hero_stats: dict[int, dict[str, Any]], enemy_picks: list[int] = None, top_n: int = 5) -> list[dict[str, Any]]:
        enemy_picks = enemy_picks or []
        candidates = []
        for hid, stats in hero_stats.items():
            if hid in enemy_picks:
                continue
            wr = stats.get("wins", 0) / max(stats.get("total", 1), 1)
            score = wr * 0.6 + min(stats.get("total", 0) / 20.0, 1.0) * 0.4
            candidates.append({"hero_id": hid, "score": score, "winrate": wr, "games": stats.get("total", 0)})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        self._fire_evolution("picks_suggested", {"count": min(top_n, len(candidates))})
        return candidates[:top_n]

    def suggest_bans(self, enemy_hero_stats: dict[int, dict[str, Any]], top_n: int = 5) -> list[int]:
        scored = []
        for hid, stats in enemy_hero_stats.items():
            wr = stats.get("wins", 0) / max(stats.get("total", 1), 1)
            scored.append((hid, wr * stats.get("total", 0)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [h for h, _ in scored[:top_n]]

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
