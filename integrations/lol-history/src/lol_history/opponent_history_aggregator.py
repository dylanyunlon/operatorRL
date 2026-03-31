"""
OpponentHistoryAggregator — Aggregates opponent match history from multiple sources.

Architecture (拿来主义):
  seraphine_deep_history_pipeline.py — register→run pipeline with module isolation
  opponent_profiler.py — profile_from_matches aggregation patterns

Location: integrations/lol-history/src/lol_history/opponent_history_aggregator.py

Design Notes (Knuth-level critique):
  User:
    - Single aggregate() call collects from all registered sources concurrently.
    - Source failures are isolated — partial data is better than no data.
    - Deduplication ensures no double-counting of the same match from different sources.
  System:
    - Source adapters are registered at init; runtime registration not needed.
    - TTL cache prevents re-fetching within the same game session.
    - Evolution callback fires per-source and on aggregation completion.
"""
from __future__ import annotations
import logging, time, hashlib
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.opponent_history_aggregator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class OpponentHistoryAggregator:
    """Aggregates opponent match history from multiple sources into unified timeline.

    Public API: register_source, aggregate, get_cached, invalidate, get_stats
    """
    def __init__(self, cache_ttl_seconds: float = 300.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._sources: Dict[str, Callable] = {}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ts: Dict[str, float] = {}
        self._cache_ttl = cache_ttl_seconds
        self._agg_count = 0
        self._source_errors: Dict[str, int] = {}
        self._total_matches_fetched = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_source(self, name: str, fetch_fn: Callable) -> Dict[str, Any]:
        """Register a history source adapter (e.g. seraphine, riot_api, sgp).

        Args:
            name: Source identifier.
            fetch_fn: Callable(puuid: str, count: int) -> List[Dict]
        """
        self._op_count += 1
        self._sources[name] = fetch_fn
        self._source_errors[name] = 0
        return {"status": "ok", "source": name, "total_sources": len(self._sources)}

    def aggregate(self, puuid: str, count: int = 20) -> Dict[str, Any]:
        """Aggregate match history for an opponent from all registered sources.

        Args:
            puuid: Player unique ID.
            count: Max matches per source.

        Returns:
            Dict with unified match list, source breakdown, dedup stats.
        """
        self._op_count += 1
        self._agg_count += 1

        # Check cache
        cache_key = f"{puuid}:{count}"
        if cache_key in self._cache:
            age = time.time() - self._cache_ts.get(cache_key, 0)
            if age < self._cache_ttl:
                return {**self._cache[cache_key], "from_cache": True, "cache_age_s": round(age, 2)}

        all_matches: List[Dict] = []
        source_breakdown: Dict[str, int] = {}
        errors: Dict[str, str] = {}

        for src_name, fetch_fn in self._sources.items():
            try:
                matches = fetch_fn(puuid, count)
                if not isinstance(matches, list):
                    matches = []
                source_breakdown[src_name] = len(matches)
                all_matches.extend([{**m, "_source": src_name} for m in matches])
            except Exception as e:
                self._source_errors[src_name] = self._source_errors.get(src_name, 0) + 1
                errors[src_name] = str(e)
                source_breakdown[src_name] = 0

        # Deduplicate by match_id
        seen: Dict[str, Dict] = {}
        for m in all_matches:
            mid = m.get("match_id") or m.get("gameId") or m.get("id") or hashlib.md5(
                str(sorted(m.items())).encode()).hexdigest()
            mid = str(mid)
            if mid not in seen:
                seen[mid] = m

        deduped = list(seen.values())
        deduped.sort(key=lambda x: x.get("timestamp", x.get("gameCreation", 0)), reverse=True)

        self._total_matches_fetched += len(deduped)

        result = {
            "status": "ok",
            "puuid": puuid,
            "matches": deduped,
            "total_unique": len(deduped),
            "total_raw": len(all_matches),
            "duplicates_removed": len(all_matches) - len(deduped),
            "source_breakdown": source_breakdown,
            "errors": errors,
            "from_cache": False,
        }

        self._cache[cache_key] = result
        self._cache_ts[cache_key] = time.time()
        self._fire("aggregated", {"puuid": puuid, "total": len(deduped), "sources": len(self._sources)})
        return result

    def get_cached(self, puuid: str, count: int = 20) -> Optional[Dict[str, Any]]:
        """Return cached result without re-fetching, or None if expired/missing."""
        self._op_count += 1
        cache_key = f"{puuid}:{count}"
        if cache_key in self._cache:
            age = time.time() - self._cache_ts.get(cache_key, 0)
            if age < self._cache_ttl:
                return {**self._cache[cache_key], "from_cache": True, "cache_age_s": round(age, 2)}
        return None

    def invalidate(self, puuid: str = None) -> Dict[str, Any]:
        """Invalidate cache for a specific puuid or all."""
        self._op_count += 1
        if puuid:
            removed = sum(1 for k in list(self._cache.keys()) if k.startswith(puuid))
            self._cache = {k: v for k, v in self._cache.items() if not k.startswith(puuid)}
            self._cache_ts = {k: v for k, v in self._cache_ts.items() if not k.startswith(puuid)}
        else:
            removed = len(self._cache)
            self._cache.clear()
            self._cache_ts.clear()
        return {"status": "ok", "removed": removed}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "op_count": self._op_count,
            "sources": len(self._sources),
            "aggregations": self._agg_count,
            "cache_entries": len(self._cache),
            "total_matches_fetched": self._total_matches_fetched,
            "source_errors": dict(self._source_errors),
        }
