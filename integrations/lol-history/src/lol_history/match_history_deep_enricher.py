"""
MatchHistoryDeepEnricher — Enriches raw match history with parsed detail data.

Architecture (拿来主义):
  Seraphine/app/lol/tools.py — parseGames: raw game list → structured game info
  Seraphine/app/lol/tools.py — parseRankInfo / parseRankInfoFromSGP patterns

Location: integrations/lol-history/src/lol_history/match_history_deep_enricher.py

Design Notes (Knuth-level critique):
  User:
    - One-call enrichment: feed raw match list → get fully parsed details with KDA,
      items, runes, lane, role, outcome, gold, damage, timeline.
    - Progressive enrichment: basic fields first, detail fields lazy-loaded on demand.
  System:
    - Stateless per-match parsing avoids accumulating stale state between enrichments.
    - Mirrors Seraphine parseGames field extraction order to maintain compatibility.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.match_history_deep_enricher.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class MatchHistoryDeepEnricher:
    """Enriches raw match history data with parsed, structured detail fields.

    Public API: enrich_match, enrich_batch, enrich_participant,
                compute_kda_stats, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._enrich_count = 0
        self._error_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _extract_participant(self, participant: Dict[str, Any]) -> Dict[str, Any]:
        """Extract structured participant data. Mirrors Seraphine parseGames field order."""
        stats = participant.get("stats", participant)
        return {
            "champion_id": participant.get("championId", stats.get("championId", 0)),
            "champion_name": participant.get("championName", ""),
            "kills": stats.get("kills", 0),
            "deaths": stats.get("deaths", 0),
            "assists": stats.get("assists", 0),
            "kda": _safe_div(stats.get("kills", 0) + stats.get("assists", 0),
                             max(stats.get("deaths", 0), 1)),
            "gold_earned": stats.get("goldEarned", 0),
            "total_damage": stats.get("totalDamageDealtToChampions", 0),
            "cs": stats.get("totalMinionsKilled", 0) + stats.get("neutralMinionsKilled", 0),
            "vision_score": stats.get("visionScore", 0),
            "items": [stats.get(f"item{i}", 0) for i in range(7)],
            "summoner_spells": [participant.get("spell1Id", 0),
                                participant.get("spell2Id", 0)],
            "rune_primary": stats.get("perkPrimaryStyle", 0),
            "rune_secondary": stats.get("perkSubStyle", 0),
            "lane": participant.get("lane", participant.get("individualPosition", "")),
            "role": participant.get("role", participant.get("teamPosition", "")),
            "team_id": participant.get("teamId", 0),
            "win": stats.get("win", False),
            "puuid": participant.get("puuid", ""),
            "summoner_name": participant.get("summonerName",
                                             participant.get("gameName", "")),
        }

    def enrich_participant(self, participant: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a single participant record."""
        self._op_count += 1
        try:
            return {"status": "ok", "participant": self._extract_participant(participant)}
        except Exception as e:
            self._error_count += 1
            return {"status": "error", "error": str(e)}

    def enrich_match(self, match_data: Dict[str, Any],
                      target_puuid: str = "") -> Dict[str, Any]:
        """Enrich a full match. Mirrors Seraphine parseGames single-game path."""
        self._op_count += 1
        self._enrich_count += 1
        try:
            info = match_data.get("info", match_data)
            participants = info.get("participants", [])
            enriched_participants = [self._extract_participant(p) for p in participants]
            # Separate teams (mirrors Seraphine separateTeams)
            team_100 = [p for p in enriched_participants if p["team_id"] == 100]
            team_200 = [p for p in enriched_participants if p["team_id"] == 200]
            target_participant = None
            if target_puuid:
                for p in enriched_participants:
                    if p["puuid"] == target_puuid:
                        target_participant = p
                        break
            result = {
                "match_id": match_data.get("metadata", {}).get("matchId",
                            match_data.get("gameId", "")),
                "game_duration": info.get("gameDuration", 0),
                "game_mode": info.get("gameMode", ""),
                "queue_id": info.get("queueId", 0),
                "game_creation": info.get("gameCreation", 0),
                "map_id": info.get("mapId", 0),
                "team_blue": team_100,
                "team_red": team_200,
                "target_participant": target_participant,
                "participant_count": len(enriched_participants),
            }
            self._fire("enriched", {"match_id": result["match_id"]})
            return {"status": "ok", "match": result}
        except Exception as e:
            self._error_count += 1
            logger.warning("Enrich failed: %s", e)
            return {"status": "error", "error": str(e)}

    def enrich_batch(self, matches: List[Dict[str, Any]],
                      target_puuid: str = "") -> Dict[str, Any]:
        """Enrich a batch of matches."""
        self._op_count += 1
        results = []
        errors = 0
        for m in matches:
            r = self.enrich_match(m, target_puuid)
            if r["status"] == "ok":
                results.append(r["match"])
            else:
                errors += 1
        return {"status": "ok", "enriched": len(results), "errors": errors,
                "matches": results}

    def compute_kda_stats(self, participants: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregate KDA stats across multiple participant records."""
        self._op_count += 1
        if not participants:
            return {"status": "ok", "avg_kda": 0.0, "avg_cs": 0.0, "count": 0}
        total_k = sum(p.get("kills", 0) for p in participants)
        total_d = sum(p.get("deaths", 0) for p in participants)
        total_a = sum(p.get("assists", 0) for p in participants)
        total_cs = sum(p.get("cs", 0) for p in participants)
        n = len(participants)
        return {
            "status": "ok",
            "avg_kills": round(total_k / n, 2),
            "avg_deaths": round(total_d / n, 2),
            "avg_assists": round(total_a / n, 2),
            "avg_kda": round(_safe_div(total_k + total_a, max(total_d, 1)), 2),
            "avg_cs": round(total_cs / n, 1),
            "count": n,
        }

    def get_stats(self) -> Dict[str, Any]:
        return {"enrich_count": self._enrich_count, "error_count": self._error_count,
                "total_ops": self._op_count}
