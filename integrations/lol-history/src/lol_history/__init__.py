"""
LoL History — Historical battle data retrieval for pre-game intelligence.

Inspired by Seraphine's LCU connector, provides structured access to
match history, game details, and ranked stats via the LCU API and SGP endpoints.

Key insight: Historical battle data of opponents is critical for live game
decision-making — knowing an opponent's preferred champions, playstyle,
and weaknesses enables adaptive strategy.

Architecture:
    LCU API / SGP → HistoryClient → MatchAnalyzer → PlayerProfiler → Agent

Seraphine reference endpoints:
    /lol-match-history/v1/products/lol/{puuid}/matches
    /lol-match-history/v1/games/{gameId}
    /lol-ranked/v1/ranked-stats/{puuid}
    /match-history-query/v1/products/lol/player/{puuid}/SUMMARY

Location: integrations/lol-history/src/lol_history/__init__.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

_EVOLUTION_KEY: str = "lol_history.client.v1"
_COMPUTE_BACKEND_DEFAULT: str = "cpu"


@dataclass
class HistoryConfig:
    """Configuration for the LCU History Client."""
    lcu_host: str = "127.0.0.1"
    lcu_port: int = 2999
    lcu_auth_token: str = ""
    max_retries: int = 3
    timeout: float = 10.0
    # SGP configuration
    sgp_enabled: bool = True
    server_region: str = "na1"


class HistoryClient:
    """Client for retrieving historical match data via LCU API.

    Adapts Seraphine's connector patterns to a stateless, production-grade
    client suitable for agentic pre-game intelligence gathering.

    Usage:
        client = HistoryClient(config=HistoryConfig(lcu_port=2999))
        url = client._build_match_history_url(puuid, beg_index=0, end_index=19)
        # In production: response = await client.fetch(url)
        matches = client.parse_match_list(raw_response)
    """

    def __init__(self, config: HistoryConfig | None = None) -> None:
        self.config = config or HistoryConfig()

    # ──────────────────── URL Builders ────────────────────

    def _build_match_history_url(
        self, puuid: str, beg_index: int = 0, end_index: int = 9
    ) -> str:
        """Build LCU match history URL.

        Reference: Seraphine getSummonerGamesByPuuid
        """
        base = f"https://{self.config.lcu_host}:{self.config.lcu_port}"
        return (
            f"{base}/lol-match-history/v1/products/lol/{puuid}/matches"
            f"?begIndex={beg_index}&endIndex={end_index}"
        )

    def _build_game_detail_url(self, game_id: int) -> str:
        """Build LCU game detail URL.

        Reference: Seraphine getGameDetailByGameId
        """
        base = f"https://{self.config.lcu_host}:{self.config.lcu_port}"
        return f"{base}/lol-match-history/v1/games/{game_id}"

    def _build_ranked_stats_url(self, puuid: str) -> str:
        """Build LCU ranked stats URL.

        Reference: Seraphine getRankedStatsByPuuid
        """
        base = f"https://{self.config.lcu_host}:{self.config.lcu_port}"
        return f"{base}/lol-ranked/v1/ranked-stats/{puuid}"

    def _build_sgp_match_url(
        self, puuid: str, beg_index: int = 0, count: int = 10
    ) -> str:
        """Build SGP match history URL.

        Reference: Seraphine getSummonerGamesByPuuidViaSGP
        """
        base = f"https://{self.config.lcu_host}:{self.config.lcu_port}"
        return (
            f"{base}/match-history-query/v1/products/lol/player/{puuid}/SUMMARY"
            f"?startIndex={beg_index}&count={count}"
        )

    # ──────────────────── Response Parsers ────────────────

    def parse_match_list(self, raw_response: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse match list response from LCU API.

        Normalizes the nested Riot response format into flat match dicts.

        Args:
            raw_response: Raw JSON response from /matches endpoint.

        Returns:
            List of normalized match dicts.
        """
        games_wrapper = raw_response.get("games", {})
        if isinstance(games_wrapper, dict):
            games = games_wrapper.get("games", [])
        elif isinstance(games_wrapper, list):
            games = games_wrapper
        else:
            return []

        result = []
        for game in games:
            normalized = {
                "game_id": game.get("gameId"),
                "game_creation": game.get("gameCreation"),
                "duration_seconds": game.get("gameDuration", 0),
                "queue_id": game.get("queueId"),
                "participants": [],
            }
            for p in game.get("participants", []):
                stats = p.get("stats", {})
                normalized["participants"].append({
                    "champion_id": p.get("championId"),
                    "win": stats.get("win", False),
                    "kills": stats.get("kills", 0),
                    "deaths": stats.get("deaths", 0),
                    "assists": stats.get("assists", 0),
                })
            result.append(normalized)
        return result

    def parse_game_detail(self, raw_detail: dict[str, Any]) -> dict[str, Any]:
        """Parse game detail response from LCU API.

        Args:
            raw_detail: Raw JSON response from /games/{gameId} endpoint.

        Returns:
            Normalized game detail dict.
        """
        participants = []
        for p in raw_detail.get("participants", []):
            stats = p.get("stats", {})
            participants.append({
                "participant_id": p.get("participantId"),
                "champion_id": p.get("championId"),
                "kills": stats.get("kills", 0),
                "deaths": stats.get("deaths", 0),
                "assists": stats.get("assists", 0),
                "total_damage": stats.get("totalDamageDealt", 0),
                "gold_earned": stats.get("goldEarned", 0),
                "win": stats.get("win", False),
            })

        teams = []
        for t in raw_detail.get("teams", []):
            teams.append({
                "team_id": t.get("teamId"),
                "win": t.get("win") == "Win",
            })

        return {
            "game_id": raw_detail.get("gameId"),
            "duration_seconds": raw_detail.get("gameDuration", 0),
            "teams": teams,
            "participants": participants,
        }
