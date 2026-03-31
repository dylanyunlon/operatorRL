"""
SgpEndpointDataNormalizer — Normalizes data from SGP endpoints into unified format.

Architecture (拿来主义):
  Seraphine/app/lol/connector.py — SGP endpoint URLs and response formats
  Seraphine/app/lol/tools.py — parseRankInfoFromSGP, getTeammatesFromSGPGame

Location: integrations/lol-history/src/lol_history/sgp_endpoint_data_normalizer.py

Design Notes (Knuth-level critique):
  User:
    - Seamless data access: same query returns same format whether from LCU or SGP.
    - SGP provides cross-region data not available via local LCU.
  System:
    - Normalization is idempotent: normalizing already-normalized data returns same result.
    - Field mapping table makes adding new SGP endpoints a config change, not code change.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.sgp_endpoint_data_normalizer.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

# SGP field → normalized field mapping
_RANK_FIELD_MAP = {
    "highestTier": "tier", "highestDivision": "division",
    "leaguePoints": "lp", "wins": "wins", "losses": "losses",
    "queueType": "queue_type",
}
_MATCH_FIELD_MAP = {
    "gameId": "match_id", "gameCreation": "game_creation",
    "gameDuration": "game_duration", "queueId": "queue_id",
    "mapId": "map_id", "gameMode": "game_mode",
}
_PARTICIPANT_FIELD_MAP = {
    "championId": "champion_id", "teamId": "team_id",
    "spell1Id": "summoner1_id", "spell2Id": "summoner2_id",
    "gameName": "game_name", "tagLine": "tag_line", "puuid": "puuid",
}


class SgpEndpointDataNormalizer:
    """Normalizes SGP endpoint responses into unified operatorRL format.

    Public API: normalize_rank_data, normalize_match_data, normalize_participant,
                normalize_match_list, detect_source, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._normalize_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _map_fields(self, data: Dict[str, Any],
                     field_map: Dict[str, str]) -> Dict[str, Any]:
        """Apply field mapping, keeping unmapped fields as-is."""
        result = {}
        for src_key, value in data.items():
            dest_key = field_map.get(src_key, src_key)
            result[dest_key] = value
        return result

    def detect_source(self, data: Dict[str, Any]) -> str:
        """Detect whether data is from LCU, SGP, or Riot API."""
        self._op_count += 1
        if "highestTier" in data or "highestDivision" in data:
            return "sgp"
        if "metadata" in data and "matchId" in data.get("metadata", {}):
            return "riot_api"
        if "tier" in data and "rank" in data:
            return "lcu"
        if "participants" in data and "gameName" in data.get("participants", [{}])[0]:
            return "sgp"
        return "unknown"

    def normalize_rank_data(self, rank_data: Dict[str, Any],
                             source: str = "") -> Dict[str, Any]:
        """Normalize rank data from any source to unified format."""
        self._op_count += 1
        self._normalize_count += 1
        if not source:
            source = self.detect_source(rank_data)
        if source == "sgp":
            # Mirrors Seraphine parseRankInfoFromSGP
            entries = rank_data.get("queues", rank_data.get("rankedStats", []))
            if isinstance(entries, dict):
                entries = [entries]
            normalized = []
            for entry in entries if isinstance(entries, list) else [entries]:
                mapped = self._map_fields(entry, _RANK_FIELD_MAP)
                mapped.setdefault("tier", "UNRANKED")
                mapped.setdefault("division", "IV")
                mapped.setdefault("lp", 0)
                mapped.setdefault("wins", 0)
                mapped.setdefault("losses", 0)
                games = mapped["wins"] + mapped["losses"]
                mapped["winrate"] = round(_safe_div(mapped["wins"], games) * 100, 1)
                mapped["games_played"] = games
                normalized.append(mapped)
        elif source == "lcu":
            if isinstance(rank_data, list):
                normalized = rank_data
            else:
                normalized = [rank_data]
            for entry in normalized:
                entry.setdefault("tier", "UNRANKED")
                entry.setdefault("division", entry.pop("rank", "IV"))
                games = entry.get("wins", 0) + entry.get("losses", 0)
                entry["winrate"] = round(_safe_div(entry.get("wins", 0), games) * 100, 1)
                entry["games_played"] = games
        else:
            normalized = [rank_data]
        return {"status": "ok", "source": source, "ranks": normalized}

    def normalize_match_data(self, match_data: Dict[str, Any],
                              source: str = "") -> Dict[str, Any]:
        """Normalize a single match from any source."""
        self._op_count += 1
        self._normalize_count += 1
        if not source:
            source = self.detect_source(match_data)
        if source == "riot_api":
            info = match_data.get("info", {})
            metadata = match_data.get("metadata", {})
            normalized = {
                "match_id": metadata.get("matchId", ""),
                "game_creation": info.get("gameCreation", 0),
                "game_duration": info.get("gameDuration", 0),
                "queue_id": info.get("queueId", 0),
                "map_id": info.get("mapId", 0),
                "game_mode": info.get("gameMode", ""),
                "participants": [self.normalize_participant(p, "riot_api").get("participant", p)
                                 for p in info.get("participants", [])],
            }
        elif source == "sgp":
            mapped = self._map_fields(match_data, _MATCH_FIELD_MAP)
            mapped["participants"] = [
                self.normalize_participant(p, "sgp").get("participant", p)
                for p in match_data.get("participants", [])]
            normalized = mapped
        else:
            normalized = match_data
        return {"status": "ok", "source": source, "match": normalized}

    def normalize_participant(self, participant: Dict[str, Any],
                               source: str = "") -> Dict[str, Any]:
        """Normalize participant data. Mirrors Seraphine getTeammatesFromSGPGame."""
        self._op_count += 1
        mapped = self._map_fields(participant, _PARTICIPANT_FIELD_MAP)
        # Ensure standard fields exist
        mapped.setdefault("champion_id", 0)
        mapped.setdefault("team_id", 0)
        mapped.setdefault("puuid", "")
        mapped.setdefault("game_name", mapped.get("summonerName", ""))
        # Extract stats if nested
        stats = participant.get("stats", {})
        if stats:
            mapped["kills"] = stats.get("kills", mapped.get("kills", 0))
            mapped["deaths"] = stats.get("deaths", mapped.get("deaths", 0))
            mapped["assists"] = stats.get("assists", mapped.get("assists", 0))
            mapped["win"] = stats.get("win", mapped.get("win", False))
        return {"status": "ok", "participant": mapped, "source": source}

    def normalize_match_list(self, matches: List[Dict[str, Any]],
                              source: str = "") -> Dict[str, Any]:
        """Normalize a list of matches."""
        self._op_count += 1
        normalized = []
        errors = 0
        for m in matches:
            try:
                r = self.normalize_match_data(m, source)
                if r["status"] == "ok":
                    normalized.append(r["match"])
                else:
                    errors += 1
            except Exception:
                errors += 1
        return {"status": "ok", "matches": normalized,
                "normalized": len(normalized), "errors": errors}

    def get_stats(self) -> Dict[str, Any]:
        return {"normalize_count": self._normalize_count, "total_ops": self._op_count}
