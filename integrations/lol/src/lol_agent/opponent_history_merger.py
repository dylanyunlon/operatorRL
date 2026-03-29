"""
Opponent History Merger — Live + Historical opponent analysis.

Identifies opponents from the live player list, merges with Seraphine
historical data, and computes threat scores for strategic decisions.

Location: integrations/lol/src/lol_agent/opponent_history_merger.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: getGameDetailByGameId response (player stats)
  - integrations/lol-history/src/lol_history/opponent_profiler.py: profiling pattern
  - integrations/lol-history/src/lol_history/pregame_scout.py: scouting pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.opponent_history_merger.v1"


class OpponentHistoryMerger:
    """Merges live opponent data with historical match records.

    Provides opponent identification, history merging, and threat
    scoring for real-time strategic advantage.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None
        self._merge_count: int = 0

    def identify_opponents(
        self,
        all_players: list[dict[str, Any]],
        my_team: str,
    ) -> list[dict[str, Any]]:
        """Identify opponent players from the full player list.

        Args:
            all_players: List of player dicts with 'team' field.
            my_team: My team identifier ('ORDER' or 'CHAOS').

        Returns:
            List of opponent player dicts.
        """
        return [p for p in all_players if p.get("team") != my_team]

    def merge(
        self,
        live_player: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Merge live player data with historical match records.

        Args:
            live_player: Current live player dict from the API.
            history: List of historical match dicts.

        Returns:
            Dict with 'live' and 'history' keys.
        """
        self._merge_count += 1
        merged = {
            "live": live_player,
            "history": history,
            "merge_timestamp": time.time(),
        }

        self._fire_evolution("opponent_merged", {
            "summoner": live_player.get("summonerName", "unknown"),
            "history_count": len(history),
        })
        return merged

    def compute_threat_score(self, merged: dict[str, Any]) -> float:
        """Compute a threat score for a merged opponent profile.

        Factors:
          - Live KDA (kills + assists vs deaths)
          - Live CS (creep score as farming efficiency proxy)
          - Historical average KDA across recent matches
          - Level advantage

        Score is normalized to [0.0, 1.0].

        Args:
            merged: Result of merge() with 'live' and 'history' keys.

        Returns:
            Threat score in [0.0, 1.0].
        """
        live = merged.get("live", {})
        history = merged.get("history", [])

        # --- Live KDA component ---
        scores = live.get("scores", {})
        kills = scores.get("kills", 0)
        deaths = max(scores.get("deaths", 0), 1)  # avoid /0
        assists = scores.get("assists", 0)
        live_kda = (kills + assists) / deaths

        # --- Live CS component ---
        cs = scores.get("creepScore", 0)
        level = live.get("level", 1)

        # --- Historical KDA component ---
        hist_kdas = []
        for match in history:
            stats = match.get("stats", {})
            hk = stats.get("kills", 0)
            hd = max(stats.get("deaths", 0), 1)
            ha = stats.get("assists", 0)
            hist_kdas.append((hk + ha) / hd)
        avg_hist_kda = sum(hist_kdas) / max(len(hist_kdas), 1)

        # --- Composite score (logistic-like normalization) ---
        raw = (
            0.35 * min(live_kda / 10.0, 1.0)
            + 0.20 * min(cs / 200.0, 1.0)
            + 0.25 * min(avg_hist_kda / 10.0, 1.0)
            + 0.20 * min(level / 18.0, 1.0)
        )
        return max(0.0, min(1.0, raw))

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
