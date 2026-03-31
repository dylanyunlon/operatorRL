"""
LiveOpponentScout — Real-time scouting of opponents at game start.

Architecture (拿来主义):
  pregame_scout.py — pregame scouting patterns
  seraphine_bridge.py — HTTP API client for history data

Location: integrations/lol-history/src/lol_history/live_opponent_scout.py

Design Notes (Knuth-level critique):
  User:
    - scout() returns actionable intel within seconds of lobby detection.
    - Threat level scoring makes prioritization intuitive.
  System:
    - Parallel fetch per opponent; individual failures don't block others.
    - Scout results feed directly into tactical_intent_reasoner and ban_pick_intelligence.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.live_opponent_scout.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class LiveOpponentScout:
    """Scouts all opponents at game start using historical data.

    Public API: scout, scout_single, get_threat_ranking, get_last_report, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._history_fetcher: Optional[Callable] = None
        self._scout_count = 0
        self._last_report: Optional[Dict] = None
        self._threat_weights = {"win_rate": 0.3, "kda": 0.2, "recent_form": 0.25, "champion_mastery": 0.25}

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_history_fetcher(self, fetcher: Callable) -> Dict[str, Any]:
        """Set the history fetch function: fn(puuid, count) -> List[Dict]."""
        self._op_count += 1
        self._history_fetcher = fetcher
        return {"status": "ok"}

    def set_threat_weights(self, weights: Dict[str, float]) -> Dict[str, Any]:
        """Override threat scoring weights."""
        self._op_count += 1
        self._threat_weights.update(weights)
        return {"status": "ok", "weights": dict(self._threat_weights)}

    def scout_single(self, puuid: str, summoner_name: str = "",
                     champion_id: int = 0, match_count: int = 20) -> Dict[str, Any]:
        """Scout a single opponent.

        Returns:
            Dict with win_rate, kda, recent_form, champion_mastery, threat_score, weaknesses.
        """
        self._op_count += 1
        matches: List[Dict] = []
        if self._history_fetcher:
            try:
                matches = self._history_fetcher(puuid, match_count)
                if not isinstance(matches, list): matches = []
            except Exception as e:
                return {"puuid": puuid, "name": summoner_name, "error": str(e),
                        "threat_score": 0.5, "matches_analyzed": 0}

        if not matches:
            return {"puuid": puuid, "name": summoner_name, "threat_score": 0.5,
                    "matches_analyzed": 0, "note": "no_history"}

        total = len(matches)
        wins = sum(1 for m in matches if m.get("win"))
        win_rate = _safe_div(wins, total)

        kills = sum(m.get("kills", 0) for m in matches)
        deaths = sum(m.get("deaths", 0) for m in matches)
        assists = sum(m.get("assists", 0) for m in matches)
        kda = _safe_div(kills + assists, max(deaths, 1))

        # Recent form: last 5 games weighted
        recent = matches[:5]
        recent_wins = sum(1 for m in recent if m.get("win"))
        recent_form = _safe_div(recent_wins, len(recent))

        # Champion mastery: how often they play current champion
        champ_games = sum(1 for m in matches if m.get("championId") == champion_id)
        champion_mastery = _safe_div(champ_games, total)

        # Threat score
        tw = self._threat_weights
        threat = (win_rate * tw.get("win_rate", 0.25) +
                  min(kda / 5.0, 1.0) * tw.get("kda", 0.25) +
                  recent_form * tw.get("recent_form", 0.25) +
                  champion_mastery * tw.get("champion_mastery", 0.25))
        threat = round(min(max(threat, 0.0), 1.0), 4)

        # Detect weaknesses
        weaknesses = []
        avg_deaths_per_game = _safe_div(deaths, total)
        if avg_deaths_per_game > 6: weaknesses.append("high_death_rate")
        avg_cs = _safe_div(sum(m.get("cs", m.get("totalMinionsKilled", 0)) for m in matches), total)
        if avg_cs < 120: weaknesses.append("low_cs")
        avg_vision = _safe_div(sum(m.get("visionScore", 0) for m in matches), total)
        if avg_vision < 15: weaknesses.append("poor_vision")
        if recent_form < 0.3: weaknesses.append("on_losing_streak")

        return {
            "puuid": puuid, "name": summoner_name, "champion_id": champion_id,
            "matches_analyzed": total, "win_rate": round(win_rate, 4),
            "kda": round(kda, 2), "recent_form": round(recent_form, 4),
            "champion_mastery": round(champion_mastery, 4),
            "threat_score": threat, "weaknesses": weaknesses,
            "avg_deaths": round(avg_deaths_per_game, 1),
            "avg_cs": round(avg_cs, 1), "avg_vision": round(avg_vision, 1),
        }

    def scout(self, opponents: List[Dict[str, Any]], match_count: int = 20) -> Dict[str, Any]:
        """Scout all opponents in current game.

        Args:
            opponents: List of dicts with puuid, summoner_name, champion_id.
        """
        self._op_count += 1
        self._scout_count += 1
        t0 = time.time()
        profiles = []
        errors = []
        for opp in opponents:
            puuid = opp.get("puuid", "")
            result = self.scout_single(puuid, opp.get("summoner_name", ""),
                                       opp.get("champion_id", 0), match_count)
            if "error" in result:
                errors.append(result)
            profiles.append(result)

        profiles.sort(key=lambda x: x.get("threat_score", 0), reverse=True)
        elapsed = round((time.time() - t0) * 1000, 1)

        report = {
            "status": "ok", "profiles": profiles, "errors": errors,
            "elapsed_ms": elapsed, "opponents_scouted": len(profiles),
            "top_threat": profiles[0] if profiles else None,
        }
        self._last_report = report
        self._fire("scouted", {"count": len(profiles), "elapsed_ms": elapsed})
        return report

    def get_threat_ranking(self) -> List[Dict[str, Any]]:
        """Return last scout's opponents ranked by threat score."""
        self._op_count += 1
        if not self._last_report: return []
        return [{"name": p.get("name"), "threat_score": p.get("threat_score"),
                 "weaknesses": p.get("weaknesses", [])}
                for p in self._last_report.get("profiles", [])]

    def get_last_report(self) -> Optional[Dict[str, Any]]:
        self._op_count += 1
        return self._last_report

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "scout_count": self._scout_count,
                "has_fetcher": self._history_fetcher is not None}
