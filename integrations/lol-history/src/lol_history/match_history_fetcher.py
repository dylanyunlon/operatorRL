"""
Match History Fetcher — Batch fetch with rate limiting and caching.

Provides batch retrieval of match histories with built-in rate limiting,
TTL-based caching, and deduplication.

Location: integrations/lol-history/src/lol_history/match_history_fetcher.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.match_history_fetcher.v1"


class MatchHistoryFetcher:
    """Batch match history fetcher with rate limiting and caching.

    Usage:
        fetcher = MatchHistoryFetcher(rate_limit_per_second=2.0, cache_ttl_seconds=300)
        fetcher.cache_put("puuid_abc", matches)
        cached = fetcher.cache_get("puuid_abc")
        batches = fetcher.split_into_batches(puuids, batch_size=5)
    """

    def __init__(
        self,
        rate_limit_per_second: float = 1.0,
        cache_ttl_seconds: float = 300.0,
        batch_size: int = 5,
    ) -> None:
        self.rate_limit_per_second = rate_limit_per_second
        self.cache_ttl_seconds = cache_ttl_seconds
        self.batch_size = batch_size
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._last_request_time: float = 0.0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def cache_put(self, key: str, matches: list[dict[str, Any]]) -> None:
        """Store matches in cache with TTL.

        Args:
            key: Cache key (typically puuid).
            matches: List of match dicts.
        """
        self._cache[key] = (time.time(), matches)

    def cache_get(self, key: str) -> Optional[list[dict[str, Any]]]:
        """Retrieve matches from cache if not expired.

        Args:
            key: Cache key.

        Returns:
            List of match dicts or None if missing/expired.
        """
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_time, matches = entry
        if time.time() - stored_time > self.cache_ttl_seconds:
            del self._cache[key]
            return None
        return matches

    def split_into_batches(
        self, puuids: list[str], batch_size: Optional[int] = None
    ) -> list[list[str]]:
        """Split puuid list into batches.

        Args:
            puuids: List of player unique IDs.
            batch_size: Override batch size. Defaults to self.batch_size.

        Returns:
            List of puuid batches.
        """
        bs = batch_size or self.batch_size
        return [puuids[i : i + bs] for i in range(0, len(puuids), bs)]

    def can_request(self) -> bool:
        """Check if rate limiter allows a request now.

        Returns:
            True if enough time has passed since last request.
        """
        if self.rate_limit_per_second <= 0:
            return False
        min_interval = 1.0 / self.rate_limit_per_second
        return (time.time() - self._last_request_time) >= min_interval

    def merge_match_lists(
        self,
        list_a: list[dict[str, Any]],
        list_b: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge two match lists, deduplicating by gameId.

        Args:
            list_a: First match list.
            list_b: Second match list.

        Returns:
            Merged deduplicated list.
        """
        seen: set = set()
        merged: list[dict[str, Any]] = []
        for m in list_a + list_b:
            gid = m.get("gameId", id(m))
            if gid not in seen:
                seen.add(gid)
                merged.append(m)
        return merged

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "match_history_fetcher",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
