"""
Live Game State — Real-time LoL game state aggregator.

Parses allgamedata JSON from the Live Client Data API into structured
game state: active player, all players, events, teams, gold differences.

Location: integrations/lol/src/lol_agent/live_game_state.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: getGameDetailByGameId response structure
  - Live Client Data API: allgamedata schema (activePlayer/allPlayers/events/gameData)
  - integrations/dota2/src/dota2_agent/dota2_bridge.py: parse_game_state pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.live_game_state.v1"


class LiveGameState:
    """Aggregated real-time LoL game state.

    Maintains a structured snapshot of the current game, updated
    from Live Client Data API allgamedata responses.

    Attributes:
        game_time: Current game time in seconds.
        game_mode: Game mode string (e.g., CLASSIC, ARAM).
        active_player: Active player data dict.
        all_players: List of all player dicts.
        events: List of game event dicts.
    """

    def __init__(self) -> None:
        self.game_time: float = 0.0
        self.game_mode: str = ""
        self.map_number: int = 0

        self.active_player: dict[str, Any] = {}
        self.all_players: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def update(self, allgamedata: dict[str, Any]) -> None:
        """Update state from allgamedata API response.

        Args:
            allgamedata: Full response dict from /liveclientdata/allgamedata.
        """
        if not allgamedata:
            return

        # --- gameData ---
        game_data = allgamedata.get("gameData", {})
        self.game_time = game_data.get("gameTime", 0.0)
        self.game_mode = game_data.get("gameMode", "")
        self.map_number = game_data.get("mapNumber", 0)

        # --- activePlayer ---
        self.active_player = allgamedata.get("activePlayer", {})

        # --- allPlayers ---
        self.all_players = allgamedata.get("allPlayers", [])

        # --- events ---
        events_wrapper = allgamedata.get("events", {})
        self.events = events_wrapper.get("Events", [])

        self._fire_evolution("state_updated", {
            "game_time": self.game_time,
            "player_count": len(self.all_players),
        })

    def get_player(self, summoner_name: str) -> Optional[dict[str, Any]]:
        """Find a player by summoner name.

        Args:
            summoner_name: Player's display name.

        Returns:
            Player dict or None if not found.
        """
        for p in self.all_players:
            if p.get("summonerName") == summoner_name:
                return p
        return None

    def get_active_player(self) -> dict[str, Any]:
        """Return active player data."""
        return self.active_player

    def get_teams(self) -> tuple[list[dict], list[dict]]:
        """Split players into ORDER and CHAOS teams.

        Returns:
            Tuple of (order_players, chaos_players).
        """
        order = [p for p in self.all_players if p.get("team") == "ORDER"]
        chaos = [p for p in self.all_players if p.get("team") == "CHAOS"]
        return order, chaos

    def compute_gold_advantage(self, my_team: str) -> float:
        """Compute gold advantage for a team.

        Uses kill/death/assist scores as proxy since Live Client API
        doesn't expose exact team gold. Approximation:
          gold_proxy = kills * 300 + assists * 150 + creepScore * 20

        Args:
            my_team: Team identifier ("ORDER" or "CHAOS").

        Returns:
            Gold difference (positive = my team ahead).
        """
        my_gold = 0.0
        enemy_gold = 0.0
        for p in self.all_players:
            scores = p.get("scores", {})
            proxy = (
                scores.get("kills", 0) * 300
                + scores.get("assists", 0) * 150
                + scores.get("creepScore", 0) * 20
            )
            if p.get("team") == my_team:
                my_gold += proxy
            else:
                enemy_gold += proxy
        return my_gold - enemy_gold

    def get_events(self) -> list[dict[str, Any]]:
        """Return all game events."""
        return self.events

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
