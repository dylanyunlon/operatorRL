"""
MultiQueuePerformanceAnalyzer — Analyzes player performance across queue types.

Architecture (拿来主义):
  Seraphine/app/lol/tools.py — parseGames queue_id filtering
  Seraphine/app/lol/connector.py — getNameMapByQueueId queue identification
  Seraphine/app/lol/tools.py — parseRankInfo multi-queue rank parsing

Location: integrations/lol-history/src/lol_history/multi_queue_performance_analyzer.py

Design Notes (Knuth-level critique):
  User:
    - "This player is Diamond in Solo/Duo but Gold in Flex" — reveals true skill level.
    - Queue-specific champion preferences surface hidden strengths.
  System:
    - Queue ID mapping follows Riot's official queue type constants.
    - Performance metrics computed per-queue independently to avoid cross-contamination.
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.multi_queue_performance_analyzer.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_QUEUE_NAMES = {
    420: "RANKED_SOLO_5x5", 440: "RANKED_FLEX_SR",
    450: "ARAM", 400: "NORMAL_DRAFT", 430: "NORMAL_BLIND",
    700: "CLASH", 830: "AI_INTRO", 840: "AI_BEGINNER", 850: "AI_INTERMEDIATE",
    900: "URF", 1020: "ONE_FOR_ALL", 1300: "NEXUS_BLITZ",
    1400: "ULTIMATE_SPELLBOOK", 1700: "ARENA",
}


class MultiQueuePerformanceAnalyzer:
    """Analyzes player performance across different game queue types.

    Public API: analyze_matches, get_queue_performance, compare_queues,
                get_primary_queue, detect_queue_anomalies, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._analyze_count = 0
        # puuid → queue_id → performance stats
        self._performances: Dict[str, Dict[int, Dict[str, Any]]] = defaultdict(
            lambda: defaultdict(lambda: {
                "games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0,
                "cs": 0, "gold": 0, "champions": defaultdict(int),
            })
        )

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def analyze_matches(self, matches: List[Dict[str, Any]],
                         target_puuid: str = "") -> Dict[str, Any]:
        """Analyze matches and accumulate per-queue performance."""
        self._op_count += 1
        self._analyze_count += 1
        processed = 0
        for match in matches:
            info = match.get("info", match)
            queue_id = info.get("queueId", 0)
            for p in info.get("participants", []):
                puuid = p.get("puuid", "")
                if target_puuid and puuid != target_puuid:
                    continue
                if not puuid:
                    continue
                stats = self._performances[puuid][queue_id]
                stats["games"] += 1
                stats["wins"] += 1 if p.get("win", False) else 0
                stats["kills"] += p.get("kills", 0)
                stats["deaths"] += p.get("deaths", 0)
                stats["assists"] += p.get("assists", 0)
                stats["cs"] += p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
                stats["gold"] += p.get("goldEarned", 0)
                champ_id = p.get("championId", 0)
                if champ_id:
                    stats["champions"][champ_id] += 1
                processed += 1
        return {"status": "ok", "processed": processed}

    def get_queue_performance(self, puuid: str,
                               queue_id: int = 420) -> Dict[str, Any]:
        """Get performance stats for a specific queue."""
        self._op_count += 1
        stats = self._performances.get(puuid, {}).get(queue_id)
        if not stats or stats["games"] == 0:
            return {"status": "ok", "puuid": puuid[:8], "queue_id": queue_id,
                    "queue_name": _QUEUE_NAMES.get(queue_id, "UNKNOWN"),
                    "has_data": False}
        g = stats["games"]
        result = {
            "queue_id": queue_id,
            "queue_name": _QUEUE_NAMES.get(queue_id, "UNKNOWN"),
            "games": g, "wins": stats["wins"],
            "winrate": round(_safe_div(stats["wins"], g) * 100, 1),
            "avg_kills": round(stats["kills"] / g, 1),
            "avg_deaths": round(stats["deaths"] / g, 1),
            "avg_assists": round(stats["assists"] / g, 1),
            "avg_kda": round(_safe_div(stats["kills"] + stats["assists"],
                                       max(stats["deaths"], 1)), 2),
            "avg_cs": round(stats["cs"] / g, 1),
            "avg_gold": round(stats["gold"] / g, 0),
            "unique_champions": len(stats["champions"]),
            "top_champions": sorted(stats["champions"].items(),
                                     key=lambda x: x[1], reverse=True)[:5],
            "has_data": True,
        }
        return {"status": "ok", "puuid": puuid[:8], **result}

    def compare_queues(self, puuid: str) -> Dict[str, Any]:
        """Compare performance across all queues for a player."""
        self._op_count += 1
        player_queues = self._performances.get(puuid, {})
        if not player_queues:
            return {"status": "ok", "puuid": puuid[:8], "queues": [], "primary_queue": None}
        queue_stats = []
        for qid, stats in player_queues.items():
            if stats["games"] > 0:
                g = stats["games"]
                queue_stats.append({
                    "queue_id": qid,
                    "queue_name": _QUEUE_NAMES.get(qid, "UNKNOWN"),
                    "games": g,
                    "winrate": round(_safe_div(stats["wins"], g) * 100, 1),
                    "avg_kda": round(_safe_div(
                        stats["kills"] + stats["assists"],
                        max(stats["deaths"], 1)), 2),
                })
        queue_stats.sort(key=lambda q: q["games"], reverse=True)
        primary = queue_stats[0] if queue_stats else None
        return {"status": "ok", "puuid": puuid[:8], "queues": queue_stats,
                "primary_queue": primary}

    def get_primary_queue(self, puuid: str) -> Dict[str, Any]:
        """Determine player's primary queue (most games played)."""
        self._op_count += 1
        comparison = self.compare_queues(puuid)
        return {"status": "ok", "primary_queue": comparison.get("primary_queue")}

    def detect_queue_anomalies(self, puuid: str) -> Dict[str, Any]:
        """Detect anomalies across queues (e.g., high rank but low casual winrate)."""
        self._op_count += 1
        player_queues = self._performances.get(puuid, {})
        anomalies = []
        solo_stats = player_queues.get(420, {})
        flex_stats = player_queues.get(440, {})
        # Compare Solo/Duo vs Flex
        if solo_stats.get("games", 0) >= 10 and flex_stats.get("games", 0) >= 10:
            solo_wr = _safe_div(solo_stats["wins"], solo_stats["games"])
            flex_wr = _safe_div(flex_stats["wins"], flex_stats["games"])
            diff = abs(solo_wr - flex_wr)
            if diff > 0.15:
                anomalies.append({
                    "type": "queue_winrate_divergence",
                    "solo_winrate": round(solo_wr * 100, 1),
                    "flex_winrate": round(flex_wr * 100, 1),
                    "diff_pct": round(diff * 100, 1),
                })
        # ARAM specialist detection
        aram_stats = player_queues.get(450, {})
        if aram_stats.get("games", 0) > 50:
            total_ranked = solo_stats.get("games", 0) + flex_stats.get("games", 0)
            if total_ranked < aram_stats["games"] * 0.3:
                anomalies.append({
                    "type": "aram_specialist",
                    "aram_games": aram_stats["games"],
                    "ranked_games": total_ranked,
                })
        self._fire("anomalies", {"count": len(anomalies)})
        return {"status": "ok", "puuid": puuid[:8], "anomalies": anomalies}

    def get_stats(self) -> Dict[str, Any]:
        total_players = len(self._performances)
        total_records = sum(
            sum(s["games"] for s in pq.values())
            for pq in self._performances.values()
        )
        return {"analyze_count": self._analyze_count, "players_tracked": total_players,
                "total_game_records": total_records, "total_ops": self._op_count}
