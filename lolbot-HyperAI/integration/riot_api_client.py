#!/usr/bin/env python3
"""
integration/riot_api_client.py — Riot Games API Client
========================================================
lolbot-HyperAI · Integration Layer

Provides access to Riot Games' public API for:
    1. Match history (for offline model training & calibration)
    2. Summoner info (rank, level, champion mastery)
    3. Champion data (from Data Dragon CDN)
    4. Live game lookup (spectator data)

The client handles:
    - API key management (from config or environment)
    - Rate limiting (Riot enforces 20/1s, 100/2min for personal keys)
    - Regional routing (NA1, EUW1, KR, etc.)
    - Response caching (champion data rarely changes)
    - Error handling with exponential backoff

This module is used by:
    - Feature pipeline: to enrich features with historical data
    - Evolution controller: to fetch match outcomes for calibration
    - Champ select advisor: for champion win rates and synergies

Note: A valid Riot API key is required. Development keys last 24h.
Production keys require Riot approval. The system degrades gracefully
without an API key — it just uses local data only.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import MessageFactory
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# Regional routing
# ---------------------------------------------------------------------------
class Region(Enum):
    """Riot API regional endpoints."""
    NA1 = "na1"
    EUW1 = "euw1"
    EUN1 = "eun1"
    KR = "kr"
    JP1 = "jp1"
    BR1 = "br1"
    LA1 = "la1"
    LA2 = "la2"
    OC1 = "oc1"
    TR1 = "tr1"
    RU = "ru"
    PH2 = "ph2"
    SG2 = "sg2"
    TH2 = "th2"
    TW2 = "tw2"
    VN2 = "vn2"


class Cluster(Enum):
    """Riot API regional clusters (for match-v5, account-v1)."""
    AMERICAS = "americas"
    EUROPE = "europe"
    ASIA = "asia"
    SEA = "sea"


_REGION_TO_CLUSTER: Dict[Region, Cluster] = {
    Region.NA1: Cluster.AMERICAS,
    Region.BR1: Cluster.AMERICAS,
    Region.LA1: Cluster.AMERICAS,
    Region.LA2: Cluster.AMERICAS,
    Region.EUW1: Cluster.EUROPE,
    Region.EUN1: Cluster.EUROPE,
    Region.TR1: Cluster.EUROPE,
    Region.RU: Cluster.EUROPE,
    Region.KR: Cluster.ASIA,
    Region.JP1: Cluster.ASIA,
    Region.OC1: Cluster.SEA,
    Region.PH2: Cluster.SEA,
    Region.SG2: Cluster.SEA,
    Region.TH2: Cluster.SEA,
    Region.TW2: Cluster.SEA,
    Region.VN2: Cluster.SEA,
}


# ---------------------------------------------------------------------------
# Rate limiter (token bucket)
# ---------------------------------------------------------------------------
class RateLimiter:
    """
    Token-bucket rate limiter for Riot API.

    Riot enforces:
        - 20 requests per 1 second
        - 100 requests per 2 minutes
    """

    def __init__(
        self,
        short_limit: int = 20,
        short_window_sec: float = 1.0,
        long_limit: int = 100,
        long_window_sec: float = 120.0,
    ) -> None:
        self._short_limit = short_limit
        self._short_window = short_window_sec
        self._long_limit = long_limit
        self._long_window = long_window_sec
        self._short_timestamps: List[float] = []
        self._long_timestamps: List[float] = []

    def acquire(self) -> float:
        """
        Check if a request is allowed.

        Returns:
            0.0 if allowed now.
            >0 = seconds to wait before retrying.
        """
        now = time.monotonic()

        # Clean old timestamps
        self._short_timestamps = [
            t for t in self._short_timestamps
            if now - t < self._short_window
        ]
        self._long_timestamps = [
            t for t in self._long_timestamps
            if now - t < self._long_window
        ]

        # Check short window
        if len(self._short_timestamps) >= self._short_limit:
            wait = self._short_timestamps[0] + self._short_window - now
            return max(0.01, wait)

        # Check long window
        if len(self._long_timestamps) >= self._long_limit:
            wait = self._long_timestamps[0] + self._long_window - now
            return max(0.01, wait)

        # Allowed — record timestamp
        self._short_timestamps.append(now)
        self._long_timestamps.append(now)
        return 0.0


# ---------------------------------------------------------------------------
# Response cache (LRU)
# ---------------------------------------------------------------------------
class LRUCache:
    """Simple LRU cache for API responses."""

    def __init__(self, max_size: int = 500, ttl_sec: float = 3600) -> None:
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_sec
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            value, timestamp = self._cache[key]
            if time.monotonic() - timestamp < self._ttl:
                self._cache.move_to_end(key)
                self._hits += 1
                return value
            else:
                del self._cache[key]
        self._misses += 1
        return None

    def put(self, key: str, value: Any) -> None:
        self._cache[key] = (value, time.monotonic())
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def stats(self) -> Dict[str, Any]:
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (
                self._hits / (self._hits + self._misses)
                if (self._hits + self._misses) > 0 else 0
            ),
        }


# ---------------------------------------------------------------------------
# Riot API Client
# ---------------------------------------------------------------------------
class RiotAPIClient:
    """
    Riot Games API client with rate limiting and caching.

    Usage:
        client = RiotAPIClient(api_key="RGAPI-xxx", region=Region.NA1)
        summoner = client.get_summoner_by_name("Faker")
        matches = client.get_match_history(summoner["puuid"], count=20)
        for match_id in matches:
            match = client.get_match(match_id)
    """

    PLATFORM_URL = "https://{region}.api.riotgames.com"
    CLUSTER_URL = "https://{cluster}.api.riotgames.com"
    DDRAGON_URL = "https://ddragon.leagueoflegends.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: Region = Region.NA1,
    ) -> None:
        self._api_key = api_key or os.environ.get("RIOT_API_KEY", "")
        self._region = region
        self._cluster = _REGION_TO_CLUSTER.get(region, Cluster.AMERICAS)
        self._rate_limiter = RateLimiter()
        self._cache = LRUCache(max_size=500, ttl_sec=1800)

        # Stats
        self._total_requests = 0
        self._total_errors = 0
        self._total_rate_limited = 0

        # DDragon version cache
        self._ddragon_version: Optional[str] = None

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    # -- Core request method --------------------------------------------

    def _request(
        self,
        url: str,
        use_api_key: bool = True,
        timeout: float = 5.0,
        max_retries: int = 2,
    ) -> Optional[Any]:
        """
        Make an HTTP request with rate limiting, retries, and caching.

        Returns parsed JSON or None on error.
        """
        # Check cache
        cached = self._cache.get(url)
        if cached is not None:
            return cached

        if use_api_key and not self._api_key:
            return None

        for attempt in range(max_retries + 1):
            # Rate limit
            wait = self._rate_limiter.acquire()
            if wait > 0:
                self._total_rate_limited += 1
                time.sleep(wait)

            self._total_requests += 1
            req = urllib.request.Request(url)
            if use_api_key:
                req.add_header("X-Riot-Token", self._api_key)
            req.add_header("Accept", "application/json")

            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
                raw = resp.read()
                data = json.loads(raw)
                self._cache.put(url, data)
                return data

            except HTTPError as e:
                self._total_errors += 1
                if e.code == 429:
                    # Rate limited by Riot — wait and retry
                    retry_after = float(
                        e.headers.get("Retry-After", "2")
                    )
                    self._total_rate_limited += 1
                    if attempt < max_retries:
                        time.sleep(retry_after)
                        continue
                elif e.code in (500, 502, 503, 504):
                    # Server error — retry with backoff
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
                return None

            except (URLError, json.JSONDecodeError, OSError, TimeoutError):
                self._total_errors += 1
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                return None

        return None

    def _platform_url(self, path: str) -> str:
        base = self.PLATFORM_URL.format(region=self._region.value)
        return f"{base}{path}"

    def _cluster_url(self, path: str) -> str:
        base = self.CLUSTER_URL.format(cluster=self._cluster.value)
        return f"{base}{path}"

    # -- Summoner API ---------------------------------------------------

    def get_summoner_by_name(
        self,
        game_name: str,
        tag_line: str = "NA1",
    ) -> Optional[Dict[str, Any]]:
        """
        Get summoner info by Riot ID (gameName#tagLine).

        Returns dict with puuid, gameName, tagLine.
        """
        url = self._cluster_url(
            f"/riot/account/v1/accounts/by-riot-id"
            f"/{game_name}/{tag_line}"
        )
        return self._request(url)

    def get_summoner_by_puuid(
        self, puuid: str,
    ) -> Optional[Dict[str, Any]]:
        """Get summoner info by PUUID."""
        url = self._platform_url(
            f"/lol/summoner/v4/summoners/by-puuid/{puuid}"
        )
        return self._request(url)

    def get_ranked_stats(
        self, summoner_id: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get ranked stats for a summoner."""
        url = self._platform_url(
            f"/lol/league/v4/entries/by-summoner/{summoner_id}"
        )
        return self._request(url)

    # -- Match History API ----------------------------------------------

    def get_match_history(
        self,
        puuid: str,
        *,
        count: int = 20,
        queue_id: Optional[int] = None,
        start: int = 0,
    ) -> Optional[List[str]]:
        """
        Get list of match IDs for a player.

        Args:
            puuid: Player's PUUID.
            count: Number of matches (max 100).
            queue_id: Filter by queue (420=ranked solo, 440=ranked flex).
            start: Starting index for pagination.

        Returns list of match IDs like ["NA1_12345", ...].
        """
        params = f"start={start}&count={min(count, 100)}"
        if queue_id is not None:
            params += f"&queue={queue_id}"
        url = self._cluster_url(
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids?{params}"
        )
        return self._request(url)

    def get_match(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full match data.

        Returns the complete match DTO with metadata, info, teams,
        participants, etc.
        """
        url = self._cluster_url(
            f"/lol/match/v5/matches/{match_id}"
        )
        return self._request(url)

    def get_match_timeline(
        self, match_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get match timeline (frame-by-frame data).

        Includes gold, xp, position data at 1-minute intervals.
        Used for training the prediction model.
        """
        url = self._cluster_url(
            f"/lol/match/v5/matches/{match_id}/timeline"
        )
        return self._request(url)

    # -- Champion Mastery API -------------------------------------------

    def get_champion_mastery(
        self,
        puuid: str,
        champion_id: Optional[int] = None,
    ) -> Optional[Any]:
        """Get champion mastery data."""
        if champion_id:
            url = self._platform_url(
                f"/lol/champion-mastery/v4/champion-masteries"
                f"/by-puuid/{puuid}/by-champion/{champion_id}"
            )
        else:
            url = self._platform_url(
                f"/lol/champion-mastery/v4/champion-masteries"
                f"/by-puuid/{puuid}/top?count=10"
            )
        return self._request(url)

    # -- Live Game (Spectator) API --------------------------------------

    def get_active_game(
        self, puuid: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get live game data (if the player is currently in a game).

        Returns None if the player is not in a game.
        """
        url = self._platform_url(
            f"/lol/spectator/v5/active-games/by-summoner/{puuid}"
        )
        return self._request(url)

    # -- Data Dragon (static data) --------------------------------------

    def get_ddragon_version(self) -> Optional[str]:
        """Get the latest Data Dragon version."""
        if self._ddragon_version:
            return self._ddragon_version
        url = f"{self.DDRAGON_URL}/api/versions.json"
        versions = self._request(url, use_api_key=False)
        if versions and isinstance(versions, list):
            self._ddragon_version = versions[0]
            return self._ddragon_version
        return None

    def get_champion_data(self) -> Optional[Dict[str, Any]]:
        """
        Get all champion data from Data Dragon.

        Returns a dict mapping champion names to their stats.
        No API key required (CDN data).
        """
        version = self.get_ddragon_version()
        if not version:
            return None
        url = (
            f"{self.DDRAGON_URL}/cdn/{version}/data/en_US/champion.json"
        )
        data = self._request(url, use_api_key=False, timeout=10.0)
        if data and "data" in data:
            return data["data"]
        return None

    def get_item_data(self) -> Optional[Dict[str, Any]]:
        """Get all item data from Data Dragon."""
        version = self.get_ddragon_version()
        if not version:
            return None
        url = f"{self.DDRAGON_URL}/cdn/{version}/data/en_US/item.json"
        data = self._request(url, use_api_key=False, timeout=10.0)
        if data and "data" in data:
            return data["data"]
        return None

    # -- Convenience methods --------------------------------------------

    def fetch_training_data(
        self,
        puuid: str,
        num_matches: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch match data for model training / calibration.

        Returns a list of simplified match summaries with:
            - participants, champions, roles
            - team stats (kills, gold, towers)
            - game duration
            - win/loss outcome
        """
        matches = self.get_match_history(
            puuid, count=num_matches, queue_id=420,  # Ranked Solo
        )
        if not matches:
            return []

        results = []
        for match_id in matches[:num_matches]:
            match_data = self.get_match(match_id)
            if not match_data:
                continue

            info = match_data.get("info", {})
            participants = info.get("participants", [])
            teams = info.get("teams", [])

            # Find the player's team
            player_team = None
            for p in participants:
                if p.get("puuid") == puuid:
                    player_team = p.get("teamId")
                    break

            if player_team is None:
                continue

            summary = {
                "match_id": match_id,
                "game_duration_sec": info.get("gameDuration", 0),
                "game_version": info.get("gameVersion", ""),
                "player_won": any(
                    p.get("win", False) for p in participants
                    if p.get("puuid") == puuid
                ),
                "participants": [
                    {
                        "champion": p.get("championName", ""),
                        "role": p.get("teamPosition", ""),
                        "team_id": p.get("teamId"),
                        "kills": p.get("kills", 0),
                        "deaths": p.get("deaths", 0),
                        "assists": p.get("assists", 0),
                        "cs": (
                            p.get("totalMinionsKilled", 0)
                            + p.get("neutralMinionsKilled", 0)
                        ),
                        "gold": p.get("goldEarned", 0),
                        "damage": p.get("totalDamageDealtToChampions", 0),
                        "vision_score": p.get("visionScore", 0),
                    }
                    for p in participants
                ],
                "teams": [
                    {
                        "team_id": t.get("teamId"),
                        "win": t.get("win", False),
                        "barons": t.get("objectives", {}).get(
                            "baron", {}
                        ).get("kills", 0),
                        "dragons": t.get("objectives", {}).get(
                            "dragon", {}
                        ).get("kills", 0),
                        "towers": t.get("objectives", {}).get(
                            "tower", {}
                        ).get("kills", 0),
                    }
                    for t in teams
                ],
            }
            results.append(summary)

        return results

    # -- Stats ----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "has_api_key": self.has_api_key,
            "region": self._region.value,
            "cluster": self._cluster.value,
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "total_rate_limited": self._total_rate_limited,
            "cache": self._cache.stats(),
            "ddragon_version": self._ddragon_version,
        }
