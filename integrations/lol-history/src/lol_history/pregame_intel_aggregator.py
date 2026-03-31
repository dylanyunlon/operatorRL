"""
PregameIntelAggregator — Aggregates all pregame intelligence into a single briefing.

Architecture (拿来主义):
  Seraphine/app/lol/tools.py — getAllyOrderByGameRole, getTeamColor, separateTeams
  Seraphine/app/lol/connector.py — champ select session data flow

Location: integrations/lol-history/src/lol_history/pregame_intel_aggregator.py

Design Notes (Knuth-level critique):
  User:
    - Single-call pregame briefing: all 10 players profiled before game starts.
    - Prioritized output: threats first, opportunities second, neutral last.
  System:
    - Aggregation is fan-out/fan-in: parallel queries to identity, rank, history modules,
      then merge. Module failures degrade gracefully (partial briefing > no briefing).
    - Memory-bounded: briefing cache limited to last 10 games.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.pregame_intel_aggregator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class PregameIntelAggregator:
    """Aggregates pregame intelligence from multiple modules into unified briefing.

    Public API: aggregate_pregame, add_intel_source, get_briefing,
                prioritize_threats, get_stats
    """
    def __init__(self, max_briefings: int = 10) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._aggregate_count = 0
        self._intel_sources: Dict[str, Any] = {}
        self._briefings: List[Dict[str, Any]] = []
        self._max_briefings = max_briefings

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def add_intel_source(self, name: str, source: Any) -> Dict[str, Any]:
        """Register an intel source module (identity_resolver, rank_mapper, etc.)."""
        self._op_count += 1
        self._intel_sources[name] = source
        return {"status": "ok", "source": name, "total": len(self._intel_sources)}

    def aggregate_pregame(self, champ_select_data: Dict[str, Any],
                           my_puuid: str = "") -> Dict[str, Any]:
        """Aggregate all pregame intel for current champ select session."""
        self._op_count += 1
        self._aggregate_count += 1
        # Extract ally and enemy teams (mirrors Seraphine separateTeams)
        my_team = champ_select_data.get("myTeam", [])
        their_team = champ_select_data.get("theirTeam", [])
        briefing = {
            "timestamp": time.time(),
            "my_puuid": my_puuid,
            "allies": [],
            "enemies": [],
            "threats": [],
            "opportunities": [],
            "source_results": {},
            "errors": [],
        }
        # Process each player
        for player in my_team:
            profile = self._profile_player(player, "ally")
            briefing["allies"].append(profile)
        for player in their_team:
            profile = self._profile_player(player, "enemy")
            briefing["enemies"].append(profile)
            # Classify as threat or opportunity
            threat_score = profile.get("threat_score", 0.5)
            if threat_score > 0.7:
                briefing["threats"].append(profile)
            elif threat_score < 0.3:
                briefing["opportunities"].append(profile)
        # Sort threats by score descending
        briefing["threats"].sort(key=lambda t: t.get("threat_score", 0), reverse=True)
        briefing["opportunities"].sort(key=lambda o: o.get("threat_score", 1))
        # Cache briefing
        self._briefings.append(briefing)
        if len(self._briefings) > self._max_briefings:
            self._briefings = self._briefings[-self._max_briefings:]
        self._fire("aggregated", {"allies": len(briefing["allies"]),
                                   "enemies": len(briefing["enemies"]),
                                   "threats": len(briefing["threats"])})
        return {"status": "ok", "briefing": briefing}

    def _profile_player(self, player_data: Dict[str, Any],
                         team_side: str) -> Dict[str, Any]:
        """Profile a single player using all available intel sources."""
        puuid = player_data.get("puuid", "")
        summoner_id = player_data.get("summonerId", 0)
        champion_id = player_data.get("championId", 0)
        profile = {
            "puuid": puuid,
            "summoner_id": summoner_id,
            "champion_id": champion_id,
            "team_side": team_side,
            "rank": {},
            "recent_champions": [],
            "threat_score": 0.5,
            "intel_confidence": 0.0,
            "source_data": {},
        }
        source_count = 0
        # Query each registered source
        for src_name, src in self._intel_sources.items():
            try:
                if hasattr(src, "get_player_intel"):
                    data = src.get_player_intel(puuid, champion_id)
                    if data:
                        profile["source_data"][src_name] = data
                        source_count += 1
            except Exception as e:
                logger.debug("Source %s failed for %s: %s", src_name, puuid[:8], e)
        profile["intel_confidence"] = round(
            _safe_div(source_count, len(self._intel_sources)), 2)
        # Compute threat score from available data
        profile["threat_score"] = self._compute_threat_score(profile)
        return profile

    def _compute_threat_score(self, profile: Dict[str, Any]) -> float:
        """Compute threat score 0-1 from aggregated intel."""
        score = 0.5  # neutral baseline
        # Boost if high rank data available
        rank_data = profile.get("source_data", {}).get("rank_mapper", {})
        if isinstance(rank_data, dict):
            numeric = rank_data.get("numeric_score", 0)
            if numeric > 600:  # Diamond+
                score += 0.2
            elif numeric < 300:  # Silver or below
                score -= 0.15
        return max(0.0, min(1.0, round(score, 3)))

    def get_briefing(self, index: int = -1) -> Dict[str, Any]:
        """Get a cached briefing by index."""
        self._op_count += 1
        if not self._briefings:
            return {"status": "ok", "briefing": None}
        idx = max(-len(self._briefings), min(index, len(self._briefings) - 1))
        return {"status": "ok", "briefing": self._briefings[idx]}

    def prioritize_threats(self, briefing: Dict[str, Any] = None) -> Dict[str, Any]:
        """Return prioritized threat list from briefing."""
        self._op_count += 1
        if briefing is None:
            briefing = self._briefings[-1] if self._briefings else {}
        threats = briefing.get("threats", [])
        return {"status": "ok", "threats": threats, "count": len(threats)}

    def get_stats(self) -> Dict[str, Any]:
        return {"aggregate_count": self._aggregate_count,
                "briefings_cached": len(self._briefings),
                "intel_sources": list(self._intel_sources.keys()),
                "total_ops": self._op_count}
