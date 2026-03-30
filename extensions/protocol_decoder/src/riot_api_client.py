"""
Riot API Client — Official Riot Games API data fetching.

Provides URL building, rate-limit tracking, response parsing, and error
handling for the Riot Games developer API.  Designed for historical data
retrieval (summoner profiles, match history, match detail) that supplements
the real-time Live Client Data captured by Fiddler.

Location: extensions/protocol_decoder/src/riot_api_client.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: retry decorator, rate-limit awareness
  - Seraphine/app/lol/opgg.py: OPGG API client, alru_cache pattern
  - leagueoflegends-optimizer articles: Riot API endpoint documentation
  - integrations/lol/src/lol_agent/seraphine_history_client.py: history fetch
  - integrations/lol/src/lol_agent/live_client_connector.py: URL builder

Design Notes (Knuth-level critique):
  User:
    - Rate-limit tracker prevents 429 cascades — user never hits bans.
    - parse_response normalises different endpoint schemas into uniform dicts.
    - handle_error returns retry guidance — caller decides policy.
  System:
    - Rate tracking is O(1) — counter + window reset, not deque scan.
    - URL building is parameterised by region — supports all Riot shards.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.riot_api_client.v1"

# ---------------------------------------------------------------------------
# Region / platform routing tables
# ---------------------------------------------------------------------------

_PLATFORM_HOSTS: Dict[str, str] = {
    "na1": "na1.api.riotgames.com",
    "euw1": "euw1.api.riotgames.com",
    "eun1": "eun1.api.riotgames.com",
    "kr": "kr.api.riotgames.com",
    "jp1": "jp1.api.riotgames.com",
    "br1": "br1.api.riotgames.com",
    "la1": "la1.api.riotgames.com",
    "la2": "la2.api.riotgames.com",
    "oc1": "oc1.api.riotgames.com",
    "tr1": "tr1.api.riotgames.com",
    "ru": "ru.api.riotgames.com",
    "ph2": "ph2.api.riotgames.com",
    "sg2": "sg2.api.riotgames.com",
    "th2": "th2.api.riotgames.com",
    "tw2": "tw2.api.riotgames.com",
    "vn2": "vn2.api.riotgames.com",
}

_REGIONAL_HOSTS: Dict[str, str] = {
    "americas": "americas.api.riotgames.com",
    "europe": "europe.api.riotgames.com",
    "asia": "asia.api.riotgames.com",
    "sea": "sea.api.riotgames.com",
}

_PLATFORM_TO_REGIONAL: Dict[str, str] = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
}

# ---------------------------------------------------------------------------
# Endpoint templates
# ---------------------------------------------------------------------------

_ENDPOINT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "summoner": {
        "by_name": "/lol/summoner/v4/summoners/by-name/{summoner_name}",
        "by_puuid": "/lol/summoner/v4/summoners/by-puuid/{puuid}",
        "by_id": "/lol/summoner/v4/summoners/{summoner_id}",
    },
    "matches": {
        "list": "/lol/match/v5/matches/by-puuid/{puuid}/ids",
        "detail": "/lol/match/v5/matches/{match_id}",
        "timeline": "/lol/match/v5/matches/{match_id}/timeline",
    },
    "league": {
        "by_summoner": "/lol/league/v4/entries/by-summoner/{summoner_id}",
    },
    "champion_mastery": {
        "by_puuid": "/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}",
    },
}


class _RateLimiter:
    """Simple sliding-window rate limiter.

    Reference: Seraphine connector.py semaphore pattern.
    """

    __slots__ = ("_limit", "_window_sec", "_requests", "_window_start")

    def __init__(self, limit: int, window_sec: float = 120.0) -> None:
        self._limit = limit
        self._window_sec = window_sec
        self._requests: int = 0
        self._window_start: float = time.time()

    def track(self) -> None:
        now = time.time()
        if now - self._window_start > self._window_sec:
            self._requests = 0
            self._window_start = now
        self._requests += 1

    def is_limited(self) -> bool:
        now = time.time()
        if now - self._window_start > self._window_sec:
            return False
        return self._requests >= self._limit

    @property
    def remaining(self) -> int:
        if self.is_limited():
            return 0
        return max(0, self._limit - self._requests)


class RiotApiClient:
    """Client for the Riot Games developer API.

    Attributes:
        request_count: Total requests tracked.
        evolution_callback: Optional callback for self-evolution events.

    Reference (拿来主义):
        - Seraphine connector.py: retry + rate-limit
        - leagueoflegends-optimizer: endpoint paths
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        region: str = "na1",
        rate_limit: int = 100,
        rate_window_sec: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._region = region.lower()
        self._rate_limiter = _RateLimiter(rate_limit, rate_window_sec)
        self._request_count: int = 0
        self._error_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def region(self) -> str:
        return self._region

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------

    def build_url(self, endpoint: str, **kwargs: Any) -> str:
        """Build a full API URL for the given endpoint.

        Args:
            endpoint: Endpoint group name (summoner, matches, league, ...).
            **kwargs: Path parameters for the template.

        Returns:
            Full HTTPS URL string.
        """
        templates = _ENDPOINT_TEMPLATES.get(endpoint, {})

        # Auto-select sub-endpoint based on provided kwargs
        if endpoint == "summoner":
            if "summoner_name" in kwargs:
                path_tmpl = templates.get("by_name", "")
            elif "puuid" in kwargs:
                path_tmpl = templates.get("by_puuid", "")
            else:
                path_tmpl = templates.get("by_id", "")
        elif endpoint == "matches":
            if "match_id" in kwargs:
                path_tmpl = templates.get("detail", "")
            else:
                path_tmpl = templates.get("list", "")
        elif endpoint == "match":
            path_tmpl = _ENDPOINT_TEMPLATES.get("matches", {}).get("detail", "")
        else:
            path_tmpl = list(templates.values())[0] if templates else f"/lol/{endpoint}"

        # Format path
        try:
            path = path_tmpl.format(**{k: quote(str(v)) for k, v in kwargs.items()})
        except KeyError:
            path = path_tmpl

        # Choose host — match endpoints use regional routing
        if endpoint in ("matches", "match"):
            regional = _PLATFORM_TO_REGIONAL.get(self._region, "americas")
            host = _REGIONAL_HOSTS.get(regional, "americas.api.riotgames.com")
        else:
            host = _PLATFORM_HOSTS.get(self._region, f"{self._region}.api.riotgames.com")

        # Query params
        query_params: Dict[str, str] = {}
        count = kwargs.get("count")
        if count is not None:
            query_params["count"] = str(count)

        qs = f"?{urlencode(query_params)}" if query_params else ""
        return f"https://{host}{path}{qs}"

    # ------------------------------------------------------------------
    # Rate limit
    # ------------------------------------------------------------------

    def track_request(self) -> None:
        """Track a request for rate-limiting and statistics."""
        self._request_count += 1
        self._rate_limiter.track()
        self._fire_evolution({"action": "request", "count": self._request_count})

    def is_rate_limited(self) -> bool:
        return self._rate_limiter.is_limited()

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def parse_response(self, endpoint: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise a raw API response into a uniform dict.

        Args:
            endpoint: Endpoint group name.
            raw: Raw JSON response dict.

        Returns:
            Normalised dict with endpoint-specific fields.
        """
        if endpoint == "summoner":
            return {
                "endpoint": "summoner",
                "id": raw.get("id", ""),
                "puuid": raw.get("puuid", ""),
                "name": raw.get("name", raw.get("gameName", "")),
                "level": raw.get("summonerLevel", 0),
                "account_id": raw.get("accountId", ""),
                "profile_icon": raw.get("profileIconId", 0),
                "revision_date": raw.get("revisionDate", 0),
            }
        elif endpoint in ("match", "matches"):
            meta = raw.get("metadata", {})
            info = raw.get("info", {})
            return {
                "endpoint": "match",
                "match_id": meta.get("matchId", raw.get("matchId", "")),
                "game_duration": info.get("gameDuration", 0),
                "game_mode": info.get("gameMode", ""),
                "participants": info.get("participants", []),
                "teams": info.get("teams", []),
                "game_version": info.get("gameVersion", ""),
            }
        else:
            return {"endpoint": endpoint, "raw": raw}

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def handle_error(self, status_code: int, message: str) -> Dict[str, Any]:
        """Return error handling guidance based on status code.

        Reference: Seraphine connector retry logic.
        """
        self._error_count += 1

        if status_code == 429:
            return {"retry": True, "wait_sec": 10.0, "reason": "rate_limited", "message": message}
        elif status_code == 403:
            return {"retry": False, "reason": "forbidden", "message": message}
        elif status_code == 401:
            return {"retry": False, "reason": "unauthorized", "message": message}
        elif status_code == 404:
            return {"retry": False, "reason": "not_found", "message": message}
        elif status_code >= 500:
            return {"retry": True, "wait_sec": 5.0, "reason": "server_error", "message": message}
        else:
            return {"retry": False, "reason": "unknown", "status_code": status_code, "message": message}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "request_count": self._request_count,
            "error_count": self._error_count,
            "rate_limited": self.is_rate_limited(),
            "remaining_requests": self._rate_limiter.remaining,
            "region": self._region,
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised in RiotApiClient")

    def __repr__(self) -> str:  # pragma: no cover
        return f"RiotApiClient(region={self._region}, requests={self._request_count})"


default_client: RiotApiClient = RiotApiClient()
