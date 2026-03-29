"""
Pregame Scout Engine — Loading screen full opponent history scan.

Scans all opponents during loading, computing threat levels from
historical performance (KDA, CS/min, winrate).

Location: integrations/lol/src/lol_agent/pregame_scout_engine.py

Reference (拿来主义):
  - Seraphine: app/lol/connector.py getRankedStatsByPuuid + match history
  - integrations/lol/src/lol_agent/opponent_history_merger.py: threat scoring
  - integrations/lol-history/src/lol_history/pregame_scout.py: scout pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.pregame_scout_engine.v1"

_DEFAULT_THREAT: float = 5.0  # Neutral threat for unknown players


class PregameScoutEngine:
    """Scans opponents during loading screen for threat assessment.

    Computes threat_level from historical KDA, CS/min, win rate.
    Results sorted by threat (highest first).

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._last_report: dict[str, Any] = {}

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def scout(
        self, opponents: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Scout all opponents and compute threat levels.

        Args:
            opponents: List of dicts with summoner_name, champion, history.
                       history items: {kda, cs_per_min, win}.

        Returns:
            List of scout reports sorted by threat (desc).
        """
        reports = []
        for opp in opponents:
            name = opp.get("summoner_name", "Unknown")
            champion = opp.get("champion", "Unknown")
            history = opp.get("history", [])

            threat = self._compute_threat(history)

            reports.append({
                "summoner_name": name,
                "champion": champion,
                "threat_level": threat,
                "games_analyzed": len(history),
            })

        # Sort by threat descending (highest threat first)
        reports.sort(key=lambda r: r["threat_level"], reverse=True)

        self._last_report = {
            "opponents": reports,
            "scouted_at": time.time(),
        }

        self._fire_evolution("scout_completed", {
            "opponent_count": len(reports),
            "max_threat": reports[0]["threat_level"] if reports else 0.0,
        })

        return reports

    def _compute_threat(self, history: list[dict[str, Any]]) -> float:
        """Compute threat level from match history.

        Formula: avg_kda * 0.5 + avg_cs * 0.3 + win_rate * 4.0
        Clamped to [0.0, 10.0].

        Returns:
            _DEFAULT_THREAT if no history, otherwise computed value.
        """
        if not history:
            return _DEFAULT_THREAT

        total_kda = sum(h.get("kda", 0.0) for h in history)
        total_cs = sum(h.get("cs_per_min", 0.0) for h in history)
        wins = sum(1 for h in history if h.get("win", False))
        n = len(history)

        avg_kda = total_kda / n
        avg_cs = total_cs / n
        win_rate = wins / n

        threat = avg_kda * 0.5 + avg_cs * 0.3 + win_rate * 4.0
        return max(0.0, min(10.0, threat))

    def get_report(self) -> dict[str, Any]:
        """Return the last scout report."""
        return dict(self._last_report)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
