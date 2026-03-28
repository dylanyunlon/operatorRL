"""
Game History ABC — Cross-game unified history data interface.

Provides abstract base class for game history providers,
enabling unified access to match history across:
- League of Legends (via LCU API / Seraphine)
- Mahjong (via Tenhou/Majsoul logs)
- Dota 2 (via OpenDota API)

Location: modules/game_history_abc.py
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

_EVOLUTION_KEY: str = "modules.game_history_abc.v1"


@dataclass
class MatchResult:
    """Unified match result across games."""
    game_id: str
    game_name: str
    player_id: str
    win: bool
    score: float = 0.0
    timestamp: float = 0.0
    duration: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayerProfile:
    """Unified player profile across games."""
    player_id: str
    game_name: str
    win_rate: float = 0.0
    total_games: int = 0
    avg_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class GameHistoryProvider(ABC):
    """Abstract base class for game history data providers.

    All game-specific history adapters must implement:
    - game_name: identifier string
    - get_match_history: retrieve match list
    - get_player_profile: retrieve player stats
    """

    @property
    @abstractmethod
    def game_name(self) -> str:
        """Unique game identifier."""
        ...

    @abstractmethod
    def get_match_history(
        self,
        player_id: str,
        limit: int = 20,
    ) -> list[MatchResult]:
        """Retrieve match history for a player.

        Args:
            player_id: Player unique identifier.
            limit: Maximum matches to return.

        Returns:
            List of MatchResult.
        """
        ...

    @abstractmethod
    def get_player_profile(self, player_id: str) -> PlayerProfile:
        """Retrieve aggregated player profile.

        Args:
            player_id: Player unique identifier.

        Returns:
            PlayerProfile with stats.
        """
        ...


class LoLHistoryAdapter(GameHistoryProvider):
    """League of Legends history adapter.

    Uses lol_history.HistoryClient under the hood.
    """

    @property
    def game_name(self) -> str:
        return "league_of_legends"

    def get_match_history(
        self,
        player_id: str,
        limit: int = 20,
    ) -> list[MatchResult]:
        """Retrieve LoL match history.

        Without a live LCU connection, returns empty list.
        In production, delegates to LCUAsyncClient.
        """
        return []

    def get_player_profile(self, player_id: str) -> PlayerProfile:
        """Retrieve LoL player profile."""
        return PlayerProfile(
            player_id=player_id,
            game_name=self.game_name,
        )


class MahjongHistoryAdapter(GameHistoryProvider):
    """Mahjong history adapter.

    Reads game logs from Tenhou/Majsoul platforms.
    """

    @property
    def game_name(self) -> str:
        return "mahjong"

    def get_match_history(
        self,
        player_id: str,
        limit: int = 20,
    ) -> list[MatchResult]:
        """Retrieve mahjong match history.

        Without log files, returns empty list.
        In production, reads Tenhou XML or Majsoul JSON logs.
        """
        return []

    def get_player_profile(self, player_id: str) -> PlayerProfile:
        """Retrieve mahjong player profile."""
        return PlayerProfile(
            player_id=player_id,
            game_name=self.game_name,
        )


class Dota2HistoryAdapter(GameHistoryProvider):
    """Dota 2 history adapter (stub).

    Would use OpenDota API in production.
    """

    @property
    def game_name(self) -> str:
        return "dota2"

    def get_match_history(
        self,
        player_id: str,
        limit: int = 20,
    ) -> list[MatchResult]:
        return []

    def get_player_profile(self, player_id: str) -> PlayerProfile:
        return PlayerProfile(
            player_id=player_id,
            game_name=self.game_name,
        )
