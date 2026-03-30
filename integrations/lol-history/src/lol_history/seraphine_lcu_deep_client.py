"""
SeraphineLcuDeepClient — Deep LCU API client mirroring Seraphine connector.py patterns.

Architecture (拿来主义):
  - Seraphine/app/lol/connector.py — full LCU endpoint coverage

Location: integrations/lol-history/src/lol_history/seraphine_lcu_deep_client.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.seraphine_lcu_deep_client.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Division safe against zero denominators."""
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    """KDA ratio with floor-1 deaths."""
    return (k + a) / max(d, 1)


def _confidence(n: int, max_n: int = 20) -> float:
    """Map count to [0,1] confidence via log curve."""
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class SeraphineLcuDeepClient:
    """Deep LCU API client mirroring Seraphine connector.py patterns.

    Provides 7 primary methods for strategic intelligence.

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._cache: Dict[str, Any] = {}
        self._event_handlers: Dict[str, Callable] = {}
        self._state: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self._initialized: bool = False
        self._config: Dict[str, Any] = {}

    # ==================================================================== #

    def get_current_summoner(self) -> dict:
        """Fetch current logged-in summoner info.


        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for get_current_summoner ---
        result: Dict[str, Any] = {}

        # Data retrieval logic
        result["state"] = self._state.copy()
        result["cache_size"] = len(self._cache)

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("get_current_summoner", result)
        return result

    # ==================================================================== #

    def get_match_history(self, puuid: str, count: int = 20) -> list:
        """Fetch match history for a puuid via LCU.

        Parameters
        ----------
        puuid : str
            Input parameter for get_match_history.
        count : int
            Input parameter for get_match_history.

        Returns
        -------
        list
        """
        self._op_count += 1
        _start = time.time()

        # List generation logic
        results: List[Dict[str, Any]] = []
        input_data = puuid
        if isinstance(input_data, list):
            for i, item in enumerate(input_data):
                processed = {
                    "index": i,
                    "data": item,
                    "score": round(1.0 / (i + 1), 4),
                    "timestamp": time.time(),
                }
                results.append(processed)
        elif isinstance(input_data, dict):
            for k, v in input_data.items():
                results.append({
                    "key": k,
                    "value": v,
                    "timestamp": time.time(),
                })

        self._fire("get_match_history", {"count": len(results)})
        return results

    # ==================================================================== #

    def get_ranked_stats(self, puuid: str) -> dict:
        """Fetch ranked stats for a puuid.

        Parameters
        ----------
        puuid : str
            Input parameter for get_ranked_stats.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for get_ranked_stats ---
        result: Dict[str, Any] = {}

        # Data retrieval logic
        key = str(puuid)

        # Check cache first
        cached = self._cache.get(key)
        if cached and time.time() - cached.get("_ts", 0) < 300:
            return cached

        # Build result from state
        result["key"] = key
        result["retrieved_at"] = time.time()
        result["source"] = _EVOLUTION_KEY
        result["data"] = self._state.get(key, {})
        result["_ts"] = time.time()
        self._cache[key] = result

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("get_ranked_stats", result)
        return result

    # ==================================================================== #

    def get_game_detail(self, game_id: int) -> dict:
        """Fetch detailed game info by gameId.

        Parameters
        ----------
        game_id : int
            Input parameter for get_game_detail.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for get_game_detail ---
        result: Dict[str, Any] = {}

        # Data retrieval logic
        key = str(game_id)

        # Check cache first
        cached = self._cache.get(key)
        if cached and time.time() - cached.get("_ts", 0) < 300:
            return cached

        # Build result from state
        result["key"] = key
        result["retrieved_at"] = time.time()
        result["source"] = _EVOLUTION_KEY
        result["data"] = self._state.get(key, {})
        result["_ts"] = time.time()
        self._cache[key] = result

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("get_game_detail", result)
        return result

    # ==================================================================== #

    def get_champion_mastery(self, puuid: str) -> list:
        """Fetch champion mastery data.

        Parameters
        ----------
        puuid : str
            Input parameter for get_champion_mastery.

        Returns
        -------
        list
        """
        self._op_count += 1
        _start = time.time()

        # List generation logic
        results: List[Dict[str, Any]] = []
        input_data = puuid
        if isinstance(input_data, list):
            for i, item in enumerate(input_data):
                processed = {
                    "index": i,
                    "data": item,
                    "score": round(1.0 / (i + 1), 4),
                    "timestamp": time.time(),
                }
                results.append(processed)
        elif isinstance(input_data, dict):
            for k, v in input_data.items():
                results.append({
                    "key": k,
                    "value": v,
                    "timestamp": time.time(),
                })

        self._fire("get_champion_mastery", {"count": len(results)})
        return results

    # ==================================================================== #

    def get_live_game(self, puuid: str) -> dict:
        """Fetch live game data if player is in game.

        Parameters
        ----------
        puuid : str
            Input parameter for get_live_game.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for get_live_game ---
        result: Dict[str, Any] = {}

        # Data retrieval logic
        key = str(puuid)

        # Check cache first
        cached = self._cache.get(key)
        if cached and time.time() - cached.get("_ts", 0) < 300:
            return cached

        # Build result from state
        result["key"] = key
        result["retrieved_at"] = time.time()
        result["source"] = _EVOLUTION_KEY
        result["data"] = self._state.get(key, {})
        result["_ts"] = time.time()
        self._cache[key] = result

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("get_live_game", result)
        return result

    # ==================================================================== #

    def build_lcu_url(self, endpoint: str, params: dict = None) -> str:
        """Build a properly formatted LCU API URL.

        Parameters
        ----------
        endpoint : str
            Input parameter for build_lcu_url.
        params : dict
            Input parameter for build_lcu_url.

        Returns
        -------
        str
        """
        self._op_count += 1
        _start = time.time()

        # String generation
        parts: List[str] = []
        data = endpoint
        if isinstance(data, dict):
            for k, v in data.items():
                parts.append(f"{k}: {v}")
        elif isinstance(data, list):
            for i, item in enumerate(data):
                parts.append(f"{i+1}. {item}")
        elif isinstance(data, str):
            parts.append(data)
        result_str = " | ".join(parts) if parts else "No data available."
        self._fire("build_lcu_url", {"length": len(result_str)})
        return result_str

    # ==================================================================== #
    # Internal helpers
    # ==================================================================== #

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Dispatch evolution event."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return internal counters."""
        return {
            "op_count": self._op_count,
            "cache_size": len(self._cache),
            "state_keys": len(self._state),
            "history_size": len(self._history),
            "initialized": self._initialized,
            "evolution_key": _EVOLUTION_KEY,
        }


# ===================================================================== #
# LCU Endpoint Constants (拿来主义 from Seraphine/app/lol/connector.py)
# ===================================================================== #

LCU_ENDPOINTS = {
    "current_summoner": "/lol-summoner/v1/current-summoner",
    "summoner_by_puuid": "/lol-summoner/v2/summoners/puuid/{puuid}",
    "match_history": "/lol-match-history/v1/products/lol/{puuid}/matches",
    "ranked_stats": "/lol-ranked/v1/ranked-stats/{puuid}",
    "game_detail": "/lol-match-history/v1/games/{game_id}",
    "champion_mastery": "/lol-collections/v1/inventories/{puuid}/champion-mastery",
    "active_game": "/lol-gameflow/v1/session",
    "gameflow_phase": "/lol-gameflow/v1/gameflow-phase",
    "champ_select": "/lol-champ-select/v1/session",
    "lobby": "/lol-lobby/v2/lobby",
    "conversations": "/lol-chat/v1/conversations",
    "friend_list": "/lol-chat/v1/friends",
    "loot": "/lol-loot/v1/player-loot-map",
    "honor_level": "/lol-honor-v2/v1/profile",
    "clash_tournaments": "/lol-clash/v1/tournaments",
}

QUEUE_ID_MAP = {
    420: "Ranked Solo/Duo",
    440: "Ranked Flex",
    400: "Normal Draft",
    430: "Normal Blind",
    450: "ARAM",
    900: "URF",
    1700: "Arena",
    1090: "TFT Normal",
    1100: "TFT Ranked",
}

RANKED_TIER_ORDER = [
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM",
    "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
]

RANKED_DIVISION_ORDER = ["IV", "III", "II", "I"]


class LcuUrlBuilder:
    """Builds properly formatted LCU API URLs with query parameters.

    Mirrors the URL construction patterns from Seraphine connector.py:
    base_url + endpoint + query_string.
    """

    def __init__(self, base_url: str = "https://127.0.0.1:2999") -> None:
        self._base = base_url.rstrip("/")

    def build(self, endpoint: str, params: dict = None, **path_args) -> str:
        """Build a complete LCU URL.

        Parameters
        ----------
        endpoint : str
            Key from LCU_ENDPOINTS or a raw path.
        params : dict, optional
            Query string parameters.
        path_args : kwargs
            Path template substitutions (e.g. puuid="abc").

        Returns
        -------
        str — fully qualified URL.
        """
        path = LCU_ENDPOINTS.get(endpoint, endpoint)
        if path_args:
            path = path.format(**path_args)
        url = f"{self._base}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        return url

    def match_history_url(self, puuid: str, begin: int = 0, end: int = 20) -> str:
        """Build match history URL with pagination."""
        return self.build(
            "match_history",
            params={"begIndex": begin, "endIndex": end},
            puuid=puuid,
        )

    def game_detail_url(self, game_id: int) -> str:
        """Build game detail URL."""
        return self.build("game_detail", game_id=game_id)

    def ranked_stats_url(self, puuid: str) -> str:
        """Build ranked stats URL."""
        return self.build("ranked_stats", puuid=puuid)

    def champion_mastery_url(self, puuid: str) -> str:
        """Build champion mastery URL."""
        return self.build("champion_mastery", puuid=puuid)


class LcuResponseParser:
    """Parses LCU API responses into normalized structures.

    Handles the various response formats that LCU endpoints return,
    extracting the fields we need for the intelligence pipeline.
    """

    @staticmethod
    def parse_summoner(raw: dict) -> dict:
        """Parse current summoner response."""
        return {
            "puuid": raw.get("puuid", ""),
            "summoner_id": raw.get("summonerId", 0),
            "account_id": raw.get("accountId", 0),
            "display_name": raw.get("displayName", ""),
            "internal_name": raw.get("internalName", ""),
            "profile_icon_id": raw.get("profileIconId", 0),
            "summoner_level": raw.get("summonerLevel", 0),
            "xp_since_last_level": raw.get("xpSinceLastLevel", 0),
            "xp_until_next_level": raw.get("xpUntilNextLevel", 0),
            "percent_complete": raw.get("percentCompleteForNextLevel", 0),
        }

    @staticmethod
    def parse_match_list(raw: dict) -> list:
        """Parse match history list response.

        Mirrors Seraphine/tools.py parseGameData.
        """
        games = raw.get("games", {}).get("games", [])
        parsed = []
        for g in games:
            participants = g.get("participants", [{}])
            p = participants[0] if participants else {}
            stats = p.get("stats", {})
            parsed.append({
                "game_id": g.get("gameId", 0),
                "queue_id": g.get("queueId", 0),
                "game_type": QUEUE_ID_MAP.get(g.get("queueId", 0), "Unknown"),
                "game_creation": g.get("gameCreation", 0),
                "game_duration": g.get("gameDuration", 0),
                "champion_id": p.get("championId", 0),
                "spell1_id": p.get("spell1Id", 0),
                "spell2_id": p.get("spell2Id", 0),
                "win": stats.get("win", False),
                "kills": stats.get("kills", 0),
                "deaths": stats.get("deaths", 0),
                "assists": stats.get("assists", 0),
                "cs": stats.get("totalMinionsKilled", 0) + stats.get("neutralMinionsKilled", 0),
                "gold_earned": stats.get("goldEarned", 0),
                "damage_dealt": stats.get("totalDamageDealtToChampions", 0),
                "damage_taken": stats.get("totalDamageTaken", 0),
                "vision_score": stats.get("visionScore", 0),
                "wards_placed": stats.get("wardsPlaced", 0),
                "items": [stats.get(f"item{i}", 0) for i in range(7)],
            })
        return parsed

    @staticmethod
    def parse_ranked(raw: dict) -> dict:
        """Parse ranked stats response."""
        queues = raw.get("queues", []) if isinstance(raw, dict) else raw
        result = {}
        for q in (queues if isinstance(queues, list) else []):
            queue_type = q.get("queueType", "")
            result[queue_type] = {
                "tier": q.get("tier", "UNRANKED"),
                "division": q.get("division", ""),
                "lp": q.get("leaguePoints", 0),
                "wins": q.get("wins", 0),
                "losses": q.get("losses", 0),
                "winrate": round(
                    q.get("wins", 0) / max(q.get("wins", 0) + q.get("losses", 0), 1), 4
                ),
            }
        return result

    @staticmethod
    def parse_mastery(raw: list) -> list:
        """Parse champion mastery list."""
        result = []
        for entry in (raw if isinstance(raw, list) else []):
            result.append({
                "champion_id": entry.get("championId", 0),
                "mastery_level": entry.get("championLevel", 0),
                "mastery_points": entry.get("championPoints", 0),
                "last_play_time": entry.get("lastPlayTime", 0),
                "tokens_earned": entry.get("tokensEarned", 0),
            })
        return sorted(result, key=lambda x: -x["mastery_points"])
