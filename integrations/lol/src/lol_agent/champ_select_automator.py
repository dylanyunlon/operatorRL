"""
Champ Select Automator — Automated champion select decisions.
Location: integrations/lol/src/lol_agent/champ_select_automator.py
Reference: Seraphine/app/lol/tools.py: autoPick, autoBan, autoComplete, autoTrade, autoSwap
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.champ_select_automator.v1"

class ChampSelectAutomator:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def suggest_pick(self, available: list[int], banned: list[int], team_comp: list[int],
                     position: str, preferences: dict[str, float] | None = None) -> dict[str, Any]:
        pool = [c for c in available if c not in banned and c not in team_comp]
        if not pool:
            return {"champion_id": 0, "confidence": 0.0}
        if preferences:
            scored = sorted(pool, key=lambda c: preferences.get(str(c), 0.5), reverse=True)
            return {"champion_id": scored[0], "confidence": preferences.get(str(scored[0]), 0.5)}
        return {"champion_id": pool[0], "confidence": 0.5}

    def suggest_ban(self, enemy_history: list[dict[str, Any]], already_banned: list[int]) -> dict[str, Any]:
        candidates = [e for e in enemy_history if e.get("championId", 0) not in already_banned]
        if not candidates:
            return {"champion_id": 0, "reason": "no_candidates"}
        best = max(candidates, key=lambda e: e.get("winrate", 0) * e.get("games", 0))
        return {"champion_id": best["championId"], "reason": "high_threat"}

    def should_trade(self, my_champion: int, teammate_champion: int,
                     my_preferred: list[int], teammate_preferred: list[int]) -> dict[str, Any]:
        return {"should_trade": teammate_champion in my_preferred and my_champion in teammate_preferred}

    def get_auto_complete_action(self, phase: str, is_my_turn: bool, selected_champion: int) -> dict[str, Any]:
        if not is_my_turn:
            return {"type": "none"}
        if selected_champion > 0:
            return {"type": "pick", "champion_id": selected_champion}
        return {"type": "none"}

    def compute_pick_priority(self, available: list[int], preferences: dict[str, float]) -> list[dict[str, Any]]:
        scored = [{"champion_id": c, "score": preferences.get(str(c), 0.0)} for c in available]
        return sorted(scored, key=lambda x: x["score"], reverse=True)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "champ_select_automator", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
