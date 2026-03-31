"""
RankTierIntelligenceMapper — Maps rank/tier data into intelligence features.

Architecture (拿来主义):
  Seraphine/app/lol/tools.py — parseRankInfo, parseRankInfoFromSGP, parseDetailRankInfo
  Seraphine/app/lol/tools.py — translateTier tier string normalization

Location: integrations/lol-history/src/lol_history/rank_tier_intelligence_mapper.py

Design Notes (Knuth-level critique):
  User:
    - Translates raw rank data into actionable intelligence: "this player is
      hardstuck Gold but was Diamond last season" → expect better mechanics than rank.
    - Detects smurfs, boosted accounts, seasonal decay patterns.
  System:
    - Rank → numeric mapping is monotonic, enabling comparison operators.
    - Dual-source support (LCU + SGP) mirrors Seraphine's parseRankInfo/parseRankInfoFromSGP.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.rank_tier_intelligence_mapper.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_TIER_ORDER = {
    "IRON": 0, "BRONZE": 1, "SILVER": 2, "GOLD": 3, "PLATINUM": 4,
    "EMERALD": 5, "DIAMOND": 6, "MASTER": 7, "GRANDMASTER": 8, "CHALLENGER": 9
}
_DIVISION_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3}


class RankTierIntelligenceMapper:
    """Maps rank/tier data from multiple sources into intelligence features.

    Public API: parse_rank, parse_rank_sgp, compute_rank_score,
                detect_anomaly, compare_ranks, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._parse_count = 0
        self._anomaly_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _tier_to_numeric(self, tier: str, division: str = "IV", lp: int = 0) -> float:
        """Convert tier+division+LP to a single numeric score (0-1000)."""
        t = _TIER_ORDER.get(tier.upper(), 0)
        d = _DIVISION_ORDER.get(division.upper(), 0)
        return t * 100 + d * 25 + min(lp, 100) * 0.25

    def parse_rank(self, rank_info: Dict[str, Any]) -> Dict[str, Any]:
        """Parse LCU-format rank info. Mirrors Seraphine parseRankInfo."""
        self._op_count += 1
        self._parse_count += 1
        queues = {}
        ranked_list = rank_info if isinstance(rank_info, list) else [rank_info]
        for entry in ranked_list:
            queue_type = entry.get("queueType", "UNKNOWN")
            tier = entry.get("tier", "UNRANKED")
            division = entry.get("rank", entry.get("division", "IV"))
            lp = entry.get("leaguePoints", 0)
            wins = entry.get("wins", 0)
            losses = entry.get("losses", 0)
            queues[queue_type] = {
                "tier": tier, "division": division, "lp": lp,
                "wins": wins, "losses": losses,
                "winrate": round(_safe_div(wins, wins + losses) * 100, 1),
                "games_played": wins + losses,
                "numeric_score": round(self._tier_to_numeric(tier, division, lp), 2),
            }
        return {"status": "ok", "queues": queues, "source": "lcu"}

    def parse_rank_sgp(self, sgp_info: Dict[str, Any]) -> Dict[str, Any]:
        """Parse SGP-format rank info. Mirrors Seraphine parseRankInfoFromSGP."""
        self._op_count += 1
        self._parse_count += 1
        queues = {}
        entries = sgp_info.get("queues", sgp_info.get("rankedStats", []))
        if isinstance(entries, list):
            for entry in entries:
                queue_type = entry.get("queueType", "UNKNOWN")
                tier = entry.get("tier", entry.get("highestTier", "UNRANKED"))
                division = entry.get("rank", entry.get("division", "IV"))
                lp = entry.get("leaguePoints", entry.get("lp", 0))
                wins = entry.get("wins", 0)
                losses = entry.get("losses", 0)
                queues[queue_type] = {
                    "tier": tier, "division": division, "lp": lp,
                    "wins": wins, "losses": losses,
                    "winrate": round(_safe_div(wins, wins + losses) * 100, 1),
                    "games_played": wins + losses,
                    "numeric_score": round(self._tier_to_numeric(tier, division, lp), 2),
                }
        return {"status": "ok", "queues": queues, "source": "sgp"}

    def compute_rank_score(self, tier: str, division: str = "IV",
                            lp: int = 0) -> Dict[str, Any]:
        """Compute numeric rank score for a single tier/division/LP."""
        self._op_count += 1
        score = self._tier_to_numeric(tier, division, lp)
        return {"status": "ok", "tier": tier, "division": division, "lp": lp,
                "numeric_score": round(score, 2)}

    def detect_anomaly(self, current_rank: Dict[str, Any],
                        historical_peak: Dict[str, Any] = None,
                        recent_winrate: float = 0.0) -> Dict[str, Any]:
        """Detect rank anomalies (smurf, boosted, decay)."""
        self._op_count += 1
        anomalies = []
        current_score = current_rank.get("numeric_score", 0)
        # Smurf detection: low rank + very high winrate
        if current_score < 400 and recent_winrate > 70:
            anomalies.append({"type": "possible_smurf", "confidence": 0.7,
                              "reason": "low_rank_high_winrate"})
        # Boosted detection: high rank + very low recent winrate
        if current_score > 500 and recent_winrate < 40 and recent_winrate > 0:
            anomalies.append({"type": "possible_boosted", "confidence": 0.5,
                              "reason": "high_rank_low_winrate"})
        # Decay detection: peak much higher than current
        if historical_peak:
            peak_score = historical_peak.get("numeric_score", 0)
            if peak_score - current_score > 200:
                anomalies.append({"type": "rank_decay", "confidence": 0.8,
                                  "drop": round(peak_score - current_score, 2)})
        if anomalies:
            self._anomaly_count += len(anomalies)
            self._fire("anomaly_detected", {"anomalies": len(anomalies)})
        return {"status": "ok", "anomalies": anomalies, "current_score": current_score}

    def compare_ranks(self, rank_a: Dict[str, Any],
                       rank_b: Dict[str, Any]) -> Dict[str, Any]:
        """Compare two rank entries and return advantage assessment."""
        self._op_count += 1
        score_a = rank_a.get("numeric_score", 0)
        score_b = rank_b.get("numeric_score", 0)
        diff = score_a - score_b
        if abs(diff) < 25:
            advantage = "even"
        elif diff > 0:
            advantage = "player_a"
        else:
            advantage = "player_b"
        return {"status": "ok", "score_a": score_a, "score_b": score_b,
                "diff": round(diff, 2), "advantage": advantage}

    def get_stats(self) -> Dict[str, Any]:
        return {"parse_count": self._parse_count, "anomaly_count": self._anomaly_count,
                "total_ops": self._op_count}
