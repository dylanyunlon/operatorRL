"""
Riot API Client - Wrapper for Riot Games developer API.

Provides rate-limited access to:
- Summoner data
- Match history
- Champion mastery
- League/rank information

Used for pre-game analysis and post-game data collection.
Reference: https://developer.riotgames.com/apis
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class Region(str, Enum):
    NA1 = "na1"
    EUW1 = "euw1"
    EUN1 = "eun1"
    KR = "kr"
    JP1 = "jp1"
    BR1 = "br1"
    LA1 = "la1"
    LA2 = "la2"
    OC1 = "oc1"
    RU = "ru"
    TR1 = "tr1"


class RoutingRegion(str, Enum):
    AMERICAS = "americas"
    EUROPE = "europe"
    ASIA = "asia"
    SEA = "sea"


# Map platform regions to routing regions
_ROUTING_MAP: dict[Region, RoutingRegion] = {
    Region.NA1: RoutingRegion.AMERICAS,
    Region.BR1: RoutingRegion.AMERICAS,
    Region.LA1: RoutingRegion.AMERICAS,
    Region.LA2: RoutingRegion.AMERICAS,
    Region.EUW1: RoutingRegion.EUROPE,
    Region.EUN1: RoutingRegion.EUROPE,
    Region.RU: RoutingRegion.EUROPE,
    Region.TR1: RoutingRegion.EUROPE,
    Region.KR: RoutingRegion.ASIA,
    Region.JP1: RoutingRegion.ASIA,
    Region.OC1: RoutingRegion.SEA,
}


@dataclass
class RiotAPIConfig:
    """Configuration for Riot API client."""
    api_key: str = ""
    region: Region = Region.NA1
    timeout: float = 10.0
    max_retries: int = 3
    requests_per_second: float = 20.0  # Development key: 20/s
    requests_per_two_minutes: int = 100  # Development key: 100/2min


@dataclass
class SummonerInfo:
    """Riot summoner information."""
    puuid: str = ""
    summoner_id: str = ""
    account_id: str = ""
    name: str = ""
    profile_icon_id: int = 0
    summoner_level: int = 0
    revision_date: int = 0


@dataclass
class RankInfo:
    """Ranked league information."""
    queue_type: str = ""
    tier: str = ""
    rank: str = ""
    league_points: int = 0
    wins: int = 0
    losses: int = 0

    @property
    def display(self) -> str:
        return f"{self.tier} {self.rank} ({self.league_points} LP)"

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        if total == 0:
            return 0.0
        return self.wins / total


@dataclass
class MatchSummary:
    """Brief match summary."""
    match_id: str = ""
    game_duration: int = 0
    game_mode: str = ""
    game_type: str = ""
    champion_name: str = ""
    champion_id: int = 0
    win: bool = False
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    gold_earned: int = 0
    items: list[int] = field(default_factory=list)
    position: str = ""


class RiotAPIClient:
    """Async client for Riot Games developer API.

    Implements per-endpoint rate limiting per Riot's specifications.

    Example::

        client = RiotAPIClient(RiotAPIConfig(api_key="RGAPI-xxx"))
        await client.connect()
        summoner = await client.get_summoner_by_name("Faker")
        history = await client.get_match_history(summoner.puuid, count=10)
        await client.close()
    """

    def __init__(self, config: RiotAPIConfig) -> None:
        self._config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._request_times: list[float] = []
        self._total_requests = 0

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=self._config.timeout,
            headers={
                "X-Riot-Token": self._config.api_key,
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, url: str, retries: int = 0) -> Optional[dict[str, Any]]:
        """Make a rate-limited API request."""
        if not self._client:
            raise RuntimeError("Client not connected")

        await self._wait_for_rate_limit()

        try:
            response = await self._client.get(url)
            self._total_requests += 1
            self._request_times.append(time.time())

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 1))
                logger.warning("Rate limited, waiting %ds", retry_after)
                await asyncio.sleep(retry_after)
                if retries < self._config.max_retries:
                    return await self._request(url, retries + 1)
            elif response.status_code == 404:
                return None
            else:
                logger.warning("API error %d for %s", response.status_code, url)
                if retries < self._config.max_retries:
                    await asyncio.sleep(1)
                    return await self._request(url, retries + 1)
        except httpx.RequestError as e:
            logger.warning("Request error: %s", e)
            if retries < self._config.max_retries:
                await asyncio.sleep(1)
                return await self._request(url, retries + 1)

        return None

    async def _wait_for_rate_limit(self) -> None:
        """Simple rate limiting: max N requests per second."""
        now = time.time()
        cutoff = now - 1.0
        recent = [t for t in self._request_times if t > cutoff]
        if len(recent) >= self._config.requests_per_second:
            sleep_time = recent[0] + 1.0 - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        # Prune old timestamps
        self._request_times = [t for t in self._request_times if t > now - 120]

    def _platform_url(self, path: str) -> str:
        return f"https://{self._config.region.value}.api.riotgames.com{path}"

    def _regional_url(self, path: str) -> str:
        routing = _ROUTING_MAP.get(self._config.region, RoutingRegion.AMERICAS)
        return f"https://{routing.value}.api.riotgames.com{path}"

    # ── Summoner Endpoints ────────────────────────────────────────────────

    async def get_summoner_by_name(self, name: str, tag: str = "NA1") -> Optional[SummonerInfo]:
        """Get summoner by Riot ID (name#tag)."""
        url = self._regional_url(f"/riot/account/v1/accounts/by-riot-id/{name}/{tag}")
        data = await self._request(url)
        if not data:
            return None
        puuid = data.get("puuid", "")

        # Get full summoner data
        url2 = self._platform_url(f"/lol/summoner/v4/summoners/by-puuid/{puuid}")
        summoner_data = await self._request(url2)
        if not summoner_data:
            return SummonerInfo(puuid=puuid, name=name)

        return SummonerInfo(
            puuid=puuid,
            summoner_id=summoner_data.get("id", ""),
            account_id=summoner_data.get("accountId", ""),
            name=summoner_data.get("name", name),
            profile_icon_id=summoner_data.get("profileIconId", 0),
            summoner_level=summoner_data.get("summonerLevel", 0),
        )

    async def get_summoner_by_puuid(self, puuid: str) -> Optional[SummonerInfo]:
        url = self._platform_url(f"/lol/summoner/v4/summoners/by-puuid/{puuid}")
        data = await self._request(url)
        if not data:
            return None
        return SummonerInfo(
            puuid=puuid,
            summoner_id=data.get("id", ""),
            name=data.get("name", ""),
            summoner_level=data.get("summonerLevel", 0),
        )

    # ── Ranked Endpoints ──────────────────────────────────────────────────

    async def get_ranked_info(self, summoner_id: str) -> list[RankInfo]:
        url = self._platform_url(f"/lol/league/v4/entries/by-summoner/{summoner_id}")
        data = await self._request(url)
        if not data or not isinstance(data, list):
            return []
        return [
            RankInfo(
                queue_type=entry.get("queueType", ""),
                tier=entry.get("tier", ""),
                rank=entry.get("rank", ""),
                league_points=entry.get("leaguePoints", 0),
                wins=entry.get("wins", 0),
                losses=entry.get("losses", 0),
            )
            for entry in data
        ]

    # ── Match History Endpoints ───────────────────────────────────────────

    async def get_match_ids(
        self, puuid: str, count: int = 20, queue: Optional[int] = None,
    ) -> list[str]:
        params = f"?count={count}"
        if queue is not None:
            params += f"&queue={queue}"
        url = self._regional_url(f"/lol/match/v5/matches/by-puuid/{puuid}/ids{params}")
        data = await self._request(url)
        if not data or not isinstance(data, list):
            return []
        return data

    async def get_match_detail(self, match_id: str) -> Optional[dict[str, Any]]:
        url = self._regional_url(f"/lol/match/v5/matches/{match_id}")
        return await self._request(url)

    async def get_match_history(
        self, puuid: str, count: int = 10,
    ) -> list[MatchSummary]:
        """Get summarized match history for a player."""
        match_ids = await self.get_match_ids(puuid, count=count)
        summaries: list[MatchSummary] = []

        for mid in match_ids:
            detail = await self.get_match_detail(mid)
            if not detail:
                continue
            info = detail.get("info", {})
            participants = info.get("participants", [])

            # Find the participant matching our puuid
            for p in participants:
                if p.get("puuid") == puuid:
                    summaries.append(MatchSummary(
                        match_id=mid,
                        game_duration=info.get("gameDuration", 0),
                        game_mode=info.get("gameMode", ""),
                        champion_name=p.get("championName", ""),
                        champion_id=p.get("championId", 0),
                        win=p.get("win", False),
                        kills=p.get("kills", 0),
                        deaths=p.get("deaths", 0),
                        assists=p.get("assists", 0),
                        cs=p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
                        gold_earned=p.get("goldEarned", 0),
                        items=[p.get(f"item{i}", 0) for i in range(7)],
                        position=p.get("teamPosition", ""),
                    ))
                    break

        return summaries

    @property
    def total_requests(self) -> int:
        return self._total_requests
