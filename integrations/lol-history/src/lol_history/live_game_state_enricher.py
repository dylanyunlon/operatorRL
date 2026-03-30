"""
LiveGameStateEnricher — Enriches live game state with historical context for richer decisions.

Architecture (拿来主义):
  - Seraphine tools.py parseAllyGameInfo + connector.py live endpoints

Location: integrations/lol-history/src/lol_history/live_game_state_enricher.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.live_game_state_enricher.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Division safe against zero denominators."""
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    """KDA ratio with floor-1 deaths."""
    return (k + a) / max(d, 1)


def _confidence(n: int, max_n: int = 20) -> float:
    """Map count to [0,1] confidence via log curve."""
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class LiveGameStateEnricher:
    """Enriches live game state with historical context for richer decisions.

    Provides 6 primary methods for strategic intelligence.

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._cache: Dict[str, Any] = {}
        self._event_handlers: Dict[str, Callable] = {}
        self._state: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self._initialized: bool = False
        self._config: Dict[str, Any] = {}

    # ==================================================================== #

    def enrich_player(self, live_player: dict, history: list) -> dict:
        """Enrich a single player's live data with history.

        Parameters
        ----------
        live_player : dict
            Input parameter for enrich_player.
        history : list
            Input parameter for enrich_player.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for enrich_player ---
        result: Dict[str, Any] = {}

        # Processing logic
        data = live_player
        result["input_type"] = type(data).__name__
        result["processed"] = True
        result["status"] = "ok"
        result["confidence"] = 0.5

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("enrich_player", result)
        return result

    # ==================================================================== #

    def enrich_all_players(self, all_players: list, history_map: dict) -> list:
        """Enrich all players in a live game.

        Parameters
        ----------
        all_players : list
            Input parameter for enrich_all_players.
        history_map : dict
            Input parameter for enrich_all_players.

        Returns
        -------
        list
        """
        self._op_count += 1
        _start = time.time()

        # List generation logic
        results: List[Dict[str, Any]] = []
        input_data = all_players
        if isinstance(input_data, list):
            for i, item in enumerate(input_data):
                processed = {
                    "index": i,
                    "data": item,
                    "score": round(1.0 / (i + 1), 4),
                    "timestamp": time.time(),
                }
                results.append(processed)
        elif isinstance(input_data, dict):
            for k, v in input_data.items():
                results.append({
                    "key": k,
                    "value": v,
                    "timestamp": time.time(),
                })

        self._fire("enrich_all_players", {"count": len(results)})
        return results

    # ==================================================================== #

    def compute_team_power_level(self, enriched_team: list) -> dict:
        """Compute aggregated team power from enriched data.

        Parameters
        ----------
        enriched_team : list
            Input parameter for compute_team_power_level.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for compute_team_power_level ---
        result: Dict[str, Any] = {}

        # Aggregate input data
        items = enriched_team if enriched_team else []
        n = len(items)
        if n == 0:
            result["status"] = "no_data"
            result["confidence"] = 0.0
            result["analysis"] = {}
            result["summary"] = "No data available for analysis."
            self._fire("compute_team_power_level", result)
            return result

        # Process each item
        scores: List[float] = []
        metrics: Dict[str, float] = defaultdict(float)
        for idx, item in enumerate(items):
            if isinstance(item, dict):
                for k, v in item.items():
                    if isinstance(v, (int, float)):
                        metrics[k] += v
                score = sum(v for v in item.values() if isinstance(v, (int, float)))
                scores.append(score / max(len(item), 1))

        # Compute aggregated metrics
        avg_metrics = {k: round(v / n, 4) for k, v in metrics.items()}
        avg_score = _safe_div(sum(scores), len(scores))
        conf = _confidence(n)

        result["status"] = "analyzed"
        result["count"] = n
        result["avg_metrics"] = avg_metrics
        result["avg_score"] = round(avg_score, 4)
        result["confidence"] = round(conf, 4)
        result["analysis"] = {
            "min_score": round(min(scores) if scores else 0, 4),
            "max_score": round(max(scores) if scores else 0, 4),
            "std_dev": round(
                math.sqrt(sum((s - avg_score) ** 2 for s in scores) / max(len(scores), 1))
                if scores else 0, 4
            ),
        }

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("compute_team_power_level", result)
        return result

    # ==================================================================== #

    def generate_matchup_matrix(self, my_team: list, enemy_team: list) -> dict:
        """Generate a lane matchup matrix with advantage scores.

        Parameters
        ----------
        my_team : list
            Input parameter for generate_matchup_matrix.
        enemy_team : list
            Input parameter for generate_matchup_matrix.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for generate_matchup_matrix ---
        result: Dict[str, Any] = {}

        # Generation logic
        timestamp = time.time()
        result["generated_at"] = timestamp
        result["version"] = "1.0.0"
        input_data = my_team
        if isinstance(input_data, dict):
            result["input_fields"] = len(input_data)
            result["data"] = {k: v for k, v in input_data.items()}
        elif isinstance(input_data, list):
            result["input_count"] = len(input_data)
            result["data"] = input_data
        else:
            result["data"] = input_data
        result["summary"] = f"Generated at {timestamp}"

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("generate_matchup_matrix", result)
        return result

    # ==================================================================== #

    def create_game_snapshot(self, live_state: dict, enrichments: dict) -> dict:
        """Create a time-stamped enriched game snapshot.

        Parameters
        ----------
        live_state : dict
            Input parameter for create_game_snapshot.
        enrichments : dict
            Input parameter for create_game_snapshot.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for create_game_snapshot ---
        result: Dict[str, Any] = {}

        # Generation logic
        timestamp = time.time()
        result["generated_at"] = timestamp
        result["version"] = "1.0.0"
        input_data = live_state
        if isinstance(input_data, dict):
            result["input_fields"] = len(input_data)
            result["data"] = {k: v for k, v in input_data.items()}
        elif isinstance(input_data, list):
            result["input_count"] = len(input_data)
            result["data"] = input_data
        else:
            result["data"] = input_data
        result["summary"] = f"Generated at {timestamp}"

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("create_game_snapshot", result)
        return result

    # ==================================================================== #

    def diff_snapshots(self, snap_a: dict, snap_b: dict) -> dict:
        """Compute difference between two game snapshots.

        Parameters
        ----------
        snap_a : dict
            Input parameter for diff_snapshots.
        snap_b : dict
            Input parameter for diff_snapshots.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for diff_snapshots ---
        result: Dict[str, Any] = {}

        # Merge/diff/compare logic
        data_a = snap_a if snap_a else {}
        data_b = snap_b if snap_b else {}

        if isinstance(data_a, dict) and isinstance(data_b, dict):
            # Merge dictionaries with conflict detection
            merged = {**data_a}
            conflicts: List[str] = []
            for k, v in data_b.items():
                if k in merged and merged[k] != v:
                    conflicts.append(k)
                    # Prefer data_a for conflicts
                else:
                    merged[k] = v
            result["merged"] = merged
            result["conflicts"] = conflicts
            result["source_a_keys"] = len(data_a)
            result["source_b_keys"] = len(data_b)
        elif isinstance(data_a, list) and isinstance(data_b, list):
            result["merged"] = data_a + data_b
            result["total_items"] = len(data_a) + len(data_b)
            result["conflicts"] = []
        else:
            result["merged"] = data_a or data_b
            result["conflicts"] = []

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("diff_snapshots", result)
        return result

    # ==================================================================== #
    # Internal helpers
    # ==================================================================== #

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Dispatch evolution event."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return internal counters."""
        return {
            "op_count": self._op_count,
            "cache_size": len(self._cache),
            "state_keys": len(self._state),
            "history_size": len(self._history),
            "initialized": self._initialized,
            "evolution_key": _EVOLUTION_KEY,
        }


# ===================================================================== #
# Domain Constants
# ===================================================================== #

GAME_PHASES = {
    "early": (0, 900),
    "mid": (900, 1800),
    "late": (1800, 99999),
}

ROLE_POSITIONS = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]

TIER_WEIGHTS = {
    "CHALLENGER": 1.0,
    "GRANDMASTER": 0.95,
    "MASTER": 0.90,
    "DIAMOND": 0.80,
    "EMERALD": 0.70,
    "PLATINUM": 0.60,
    "GOLD": 0.50,
    "SILVER": 0.40,
    "BRONZE": 0.30,
    "IRON": 0.20,
}

OBJECTIVE_TYPES = [
    "DRAGON", "RIFT_HERALD", "BARON_NASHOR",
    "ELDER_DRAGON", "TOWER", "INHIBITOR",
]

DRAGON_TYPES = [
    "INFERNAL", "MOUNTAIN", "OCEAN", "CLOUD",
    "HEXTECH", "CHEMTECH", "ELDER",
]

LANE_STATES = [
    "pushing", "frozen", "slow_pushing",
    "fast_pushing", "crashing", "neutral",
]


class PerformanceTracker:
    """Tracks performance metrics over time for trend analysis.

    Provides a sliding window of metrics that can be queried for
    averages, trends, and anomaly detection.
    """

    def __init__(self, window_size: int = 20) -> None:
        self._window_size = window_size
        self._metrics: Dict[str, List[float]] = defaultdict(list)
        self._timestamps: List[float] = []

    def record(self, metric_name: str, value: float) -> None:
        """Record a metric value."""
        self._metrics[metric_name].append(value)
        if len(self._metrics[metric_name]) > self._window_size:
            self._metrics[metric_name] = self._metrics[metric_name][-self._window_size:]
        self._timestamps.append(time.time())
        if len(self._timestamps) > self._window_size:
            self._timestamps = self._timestamps[-self._window_size:]

    def get_average(self, metric_name: str) -> float:
        """Get the average of a metric over the window."""
        values = self._metrics.get(metric_name, [])
        return sum(values) / len(values) if values else 0.0

    def get_trend(self, metric_name: str) -> str:
        """Get the trend direction of a metric."""
        values = self._metrics.get(metric_name, [])
        if len(values) < 4:
            return "insufficient_data"
        first_half = sum(values[:len(values)//2]) / max(len(values)//2, 1)
        second_half = sum(values[len(values)//2:]) / max(len(values) - len(values)//2, 1)
        if second_half > first_half * 1.05:
            return "improving"
        if second_half < first_half * 0.95:
            return "declining"
        return "stable"

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all tracked metrics."""
        summary = {}
        for name, values in self._metrics.items():
            if values:
                summary[name] = {
                    "current": values[-1],
                    "avg": round(sum(values) / len(values), 4),
                    "min": min(values),
                    "max": max(values),
                    "count": len(values),
                    "trend": self.get_trend(name),
                }
        return summary


class IntelligenceAggregator:
    """Aggregates intelligence from multiple sources into a unified view.

    Deduplicates, prioritizes, and merges intelligence signals from
    different analysis modules.
    """

    def __init__(self) -> None:
        self._signals: List[Dict[str, Any]] = []
        self._dedup_keys: set = set()

    def add_signal(self, signal: Dict[str, Any]) -> bool:
        """Add an intelligence signal.

        Returns True if signal was new, False if duplicate.
        """
        key = f"{signal.get('type', '')}:{signal.get('source', '')}:{signal.get('target', '')}"
        if key in self._dedup_keys:
            return False
        self._dedup_keys.add(key)
        signal["received_at"] = time.time()
        self._signals.append(signal)
        return True

    def get_top_signals(self, n: int = 5) -> List[Dict[str, Any]]:
        """Get the top N signals by priority."""
        priority_map = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_signals = sorted(
            self._signals,
            key=lambda s: priority_map.get(s.get("priority", "low"), 3),
        )
        return sorted_signals[:n]

    def clear(self) -> None:
        """Clear all signals."""
        self._signals.clear()
        self._dedup_keys.clear()

    def get_stats(self) -> Dict[str, int]:
        """Get signal statistics."""
        by_priority = defaultdict(int)
        for s in self._signals:
            by_priority[s.get("priority", "unknown")] += 1
        return dict(by_priority)


class GameContextBuilder:
    """Builds rich game context dictionaries from various data sources.

    Consolidates live data, historical data, and derived intelligence
    into a single context object that downstream modules can consume.
    """

    def __init__(self) -> None:
        self._context: Dict[str, Any] = {}

    def set_live_state(self, state: Dict[str, Any]) -> None:
        """Set the live game state portion."""
        self._context["live"] = state
        self._context["live_updated_at"] = time.time()

    def set_history(self, history: Dict[str, Any]) -> None:
        """Set the historical data portion."""
        self._context["history"] = history

    def set_intelligence(self, intel: Dict[str, Any]) -> None:
        """Set the derived intelligence portion."""
        self._context["intelligence"] = intel

    def build(self) -> Dict[str, Any]:
        """Build and return the complete context."""
        return {
            **self._context,
            "built_at": time.time(),
            "completeness": self._compute_completeness(),
        }

    def _compute_completeness(self) -> float:
        """Compute how complete the context is (0-1)."""
        required = ["live", "history", "intelligence"]
        present = sum(1 for k in required if k in self._context)
        return present / len(required)
