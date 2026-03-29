"""
Matchup Knowledge Base — Persistent champion matchup winrates and strategies.

Location: integrations/lol/src/lol_agent/matchup_knowledge_base.py

Reference (拿来主义):
  - integrations/lol-history/src/lol_history/matchup_database.py: storage pattern
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.matchup_knowledge_base.v1"

class MatchupKnowledgeBase:
    """Persistent matchup knowledge store."""

    def __init__(self) -> None:
        self._matchups: dict[tuple[str, str], dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record(self, my_champ: str, enemy_champ: str, won: bool, notes: str = "") -> None:
        key = (my_champ, enemy_champ)
        if key not in self._matchups:
            self._matchups[key] = {"wins": 0, "losses": 0, "total": 0, "notes": []}
        self._matchups[key]["total"] += 1
        if won:
            self._matchups[key]["wins"] += 1
        else:
            self._matchups[key]["losses"] += 1
        if notes:
            self._matchups[key]["notes"].append(notes)
        self._fire_evolution("matchup_recorded", {"key": f"{my_champ}_vs_{enemy_champ}"})

    def query(self, my_champ: str, enemy_champ: str) -> dict[str, Any]:
        key = (my_champ, enemy_champ)
        entry = self._matchups.get(key, {"wins": 0, "losses": 0, "total": 0, "notes": []})
        wr = entry["wins"] / max(entry["total"], 1)
        return {**entry, "winrate": wr}

    def get_all_matchups(self, my_champ: str) -> list[dict[str, Any]]:
        result = []
        for (mc, ec), v in self._matchups.items():
            if mc == my_champ:
                wr = v["wins"] / max(v["total"], 1)
                result.append({"enemy": ec, "winrate": wr, "total": v["total"]})
        result.sort(key=lambda x: x["total"], reverse=True)
        return result

    def size(self) -> int:
        return len(self._matchups)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
