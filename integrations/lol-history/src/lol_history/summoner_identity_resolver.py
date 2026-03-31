"""
SummonerIdentityResolver — Resolves summoner identity across data sources.

Architecture (拿来主义):
  Seraphine/app/lol/connector.py — getLoginSummonerByPid, getLolClientPid patterns
  Seraphine/app/lol/tools.py — getNameTagLineFromGame puuid↔name resolution

Location: integrations/lol-history/src/lol_history/summoner_identity_resolver.py

Design Notes (Knuth-level critique):
  User:
    - Unified identity: one puuid → all aliases (name, tag, region, account_id).
    - Stale cache auto-eviction via TTL prevents name-change blindspots.
  System:
    - Three-tier resolution: LCU local → SGP regional → Riot API fallback.
    - puuid is the canonical key; all other identifiers are secondary indices.
"""
from __future__ import annotations
import logging, time, hashlib
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.summoner_identity_resolver.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class SummonerIdentityResolver:
    """Resolves summoner identity from puuid, name, or tag across data sources.

    Public API: resolve_by_puuid, resolve_by_name, resolve_from_game,
                batch_resolve, invalidate, get_stats
    """
    def __init__(self, cache_ttl: float = 3600.0, max_cache: int = 2000) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._name_index: Dict[str, str] = {}  # normalized_name → puuid
        self._cache_ttl = cache_ttl
        self._max_cache = max_cache
        self._resolve_count = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._sources: Dict[str, Any] = {}  # registered data sources

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _normalize_name(self, name: str) -> str:
        return name.strip().lower().replace(" ", "")

    def _evict_expired(self):
        now = time.time()
        expired = [k for k, v in self._cache.items()
                   if now - v.get("_cached_at", 0) > self._cache_ttl]
        for k in expired:
            entry = self._cache.pop(k, {})
            norm = self._normalize_name(entry.get("game_name", ""))
            self._name_index.pop(norm, None)

    def _put_cache(self, puuid: str, identity: Dict[str, Any]):
        self._evict_expired()
        if len(self._cache) >= self._max_cache:
            self._cache.popitem(last=False)
        identity["_cached_at"] = time.time()
        self._cache[puuid] = identity
        name = identity.get("game_name", "")
        if name:
            self._name_index[self._normalize_name(name)] = puuid

    def register_source(self, name: str, source: Any) -> Dict[str, Any]:
        """Register a data source (lcu_client, sgp_client, riot_api)."""
        self._op_count += 1
        self._sources[name] = source
        return {"status": "ok", "source": name, "total_sources": len(self._sources)}

    def resolve_by_puuid(self, puuid: str) -> Dict[str, Any]:
        """Resolve summoner identity from puuid. Cache-first, then sources."""
        self._op_count += 1
        self._resolve_count += 1
        if puuid in self._cache:
            entry = self._cache[puuid]
            if time.time() - entry.get("_cached_at", 0) <= self._cache_ttl:
                self._cache_hits += 1
                return {"status": "ok", "identity": entry, "source": "cache"}
        self._cache_misses += 1
        # Build identity from available sources
        identity = {"puuid": puuid, "game_name": "", "tag_line": "",
                     "summoner_id": "", "account_id": "", "region": "",
                     "profile_icon_id": 0, "summoner_level": 0}
        resolved_from = "none"
        for src_name, src in self._sources.items():
            try:
                if hasattr(src, "get_summoner_by_puuid"):
                    data = src.get_summoner_by_puuid(puuid)
                    if data and isinstance(data, dict):
                        identity.update({k: v for k, v in data.items()
                                         if k in identity and v})
                        resolved_from = src_name
                        break
            except Exception as e:
                logger.debug("Source %s failed for puuid %s: %s", src_name, puuid[:8], e)
        self._put_cache(puuid, identity)
        self._fire("resolved", {"puuid": puuid[:8], "source": resolved_from})
        return {"status": "ok", "identity": identity, "source": resolved_from}

    def resolve_by_name(self, game_name: str, tag_line: str = "") -> Dict[str, Any]:
        """Resolve summoner identity from name+tag."""
        self._op_count += 1
        self._resolve_count += 1
        norm = self._normalize_name(game_name)
        if norm in self._name_index:
            puuid = self._name_index[norm]
            if puuid in self._cache:
                entry = self._cache[puuid]
                if time.time() - entry.get("_cached_at", 0) <= self._cache_ttl:
                    self._cache_hits += 1
                    return {"status": "ok", "identity": entry, "source": "cache"}
        self._cache_misses += 1
        identity = {"puuid": "", "game_name": game_name, "tag_line": tag_line,
                     "summoner_id": "", "account_id": "", "region": "",
                     "profile_icon_id": 0, "summoner_level": 0}
        for src_name, src in self._sources.items():
            try:
                if hasattr(src, "get_summoner_by_name"):
                    data = src.get_summoner_by_name(game_name, tag_line)
                    if data and isinstance(data, dict):
                        identity.update({k: v for k, v in data.items()
                                         if k in identity and v})
                        if identity["puuid"]:
                            self._put_cache(identity["puuid"], identity)
                        break
            except Exception as e:
                logger.debug("Source %s failed for name %s: %s", src_name, game_name, e)
        return {"status": "ok", "identity": identity}

    def resolve_from_game(self, game_data: Dict[str, Any],
                           target_puuid: str = "") -> Dict[str, Any]:
        """Extract and resolve identities from Seraphine game data structure.

        Mirrors Seraphine/tools.py getNameTagLineFromGame pattern.
        """
        self._op_count += 1
        participants = game_data.get("participants", game_data.get("participantIdentities", []))
        resolved = []
        for p in participants:
            puuid = p.get("puuid", "")
            name = p.get("gameName", p.get("summonerName", ""))
            tag = p.get("tagLine", "")
            if not puuid:
                continue
            identity = {"puuid": puuid, "game_name": name, "tag_line": tag,
                         "champion_id": p.get("championId", 0),
                         "team_id": p.get("teamId", 0)}
            self._put_cache(puuid, identity)
            resolved.append(identity)
        return {"status": "ok", "resolved_count": len(resolved),
                "target_found": any(r["puuid"] == target_puuid for r in resolved),
                "identities": resolved}

    def batch_resolve(self, puuids: List[str]) -> Dict[str, Any]:
        """Resolve multiple puuids in batch."""
        self._op_count += 1
        results = {}
        for puuid in puuids:
            r = self.resolve_by_puuid(puuid)
            results[puuid] = r.get("identity", {})
        return {"status": "ok", "resolved": len(results), "identities": results}

    def invalidate(self, puuid: str) -> Dict[str, Any]:
        """Invalidate cached identity for a puuid."""
        self._op_count += 1
        entry = self._cache.pop(puuid, None)
        if entry:
            norm = self._normalize_name(entry.get("game_name", ""))
            self._name_index.pop(norm, None)
        return {"status": "ok", "invalidated": puuid, "was_cached": entry is not None}

    def get_stats(self) -> Dict[str, Any]:
        hit_rate = _safe_div(self._cache_hits, self._cache_hits + self._cache_misses)
        return {"resolve_count": self._resolve_count, "cache_size": len(self._cache),
                "cache_hit_rate": round(hit_rate, 4), "sources": list(self._sources.keys()),
                "total_ops": self._op_count}
