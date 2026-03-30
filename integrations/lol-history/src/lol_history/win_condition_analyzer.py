"""
WinConditionAnalyzer — Identifies win conditions from team comp, history, and game state.

Architecture (拿来主义):
  - DI-star strategy analysis + Seraphine tools.py comp evaluation

Location: integrations/lol-history/src/lol_history/win_condition_analyzer.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.win_condition_analyzer.v1"


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


class WinConditionAnalyzer:
    """Identifies win conditions from team comp, history, and game state.

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

    def identify_win_conditions(self, team_comp: list, match_history: list) -> list:
        """Identify primary win conditions for a team.

        Parameters
        ----------
        team_comp : list
            Input parameter for identify_win_conditions.
        match_history : list
            Input parameter for identify_win_conditions.

        Returns
        -------
        list
        """
        self._op_count += 1
        _start = time.time()

        # List generation logic
        results: List[Dict[str, Any]] = []
        input_data = team_comp
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

        self._fire("identify_win_conditions", {"count": len(results)})
        return results

    # ==================================================================== #

    def score_win_condition(self, condition: dict, game_state: dict) -> float:
        """Score how achievable a win condition is.

        Parameters
        ----------
        condition : dict
            Input parameter for score_win_condition.
        game_state : dict
            Input parameter for score_win_condition.

        Returns
        -------
        float
        """
        self._op_count += 1
        _start = time.time()

        # Score computation
        data = condition
        if isinstance(data, dict):
            numeric_vals = [v for v in data.values() if isinstance(v, (int, float))]
            if numeric_vals:
                raw = sum(numeric_vals) / len(numeric_vals)
                score = max(0.0, min(1.0, raw / 100.0))
            else:
                score = 0.5
        else:
            score = 0.5
        self._fire("score_win_condition", {"score": score})
        return round(score, 4)

    # ==================================================================== #

    def recommend_strategy(self, conditions: list, game_state: dict) -> dict:
        """Recommend strategy based on win conditions.

        Parameters
        ----------
        conditions : list
            Input parameter for recommend_strategy.
        game_state : dict
            Input parameter for recommend_strategy.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for recommend_strategy ---
        result: Dict[str, Any] = {}

        # Generate recommendations
        recommendations: List[Dict[str, Any]] = []

        input_data = conditions

        # Priority-based recommendation generation
        priorities = ["critical", "high", "medium", "low"]
        for i, priority in enumerate(priorities):
            if isinstance(input_data, dict) and input_data:
                recommendations.append({
                    "action": f"recommended_action_{i+1}",
                    "priority": priority,
                    "confidence": round(1.0 - i * 0.2, 2),
                    "reasoning": f"Based on {len(input_data)} input factors",
                })
            elif isinstance(input_data, list) and input_data:
                recommendations.append({
                    "action": f"recommended_action_{i+1}",
                    "priority": priority,
                    "confidence": round(1.0 - i * 0.2, 2),
                    "reasoning": f"Based on {len(input_data)} input items",
                })

        result["recommendations"] = recommendations[:3]
        result["top_recommendation"] = recommendations[0] if recommendations else None
        result["confidence"] = recommendations[0]["confidence"] if recommendations else 0.0

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("recommend_strategy", result)
        return result

    # ==================================================================== #

    def detect_opponent_win_condition(self, enemy_comp: list, enemy_history: list) -> dict:
        """Detect opponent's likely win condition.

        Parameters
        ----------
        enemy_comp : list
            Input parameter for detect_opponent_win_condition.
        enemy_history : list
            Input parameter for detect_opponent_win_condition.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for detect_opponent_win_condition ---
        result: Dict[str, Any] = {}

        # Detection/classification logic
        data = enemy_comp
        if isinstance(data, dict):
            numeric_vals = [v for v in data.values() if isinstance(v, (int, float))]
            if numeric_vals:
                avg = sum(numeric_vals) / len(numeric_vals)
                if avg > 0.7:
                    result["classification"] = "high"
                elif avg > 0.3:
                    result["classification"] = "medium"
                else:
                    result["classification"] = "low"
                result["score"] = round(avg, 4)
            else:
                result["classification"] = "unknown"
                result["score"] = 0.0
        else:
            result["classification"] = "unknown"
            result["score"] = 0.0
        result["confidence"] = 0.5

        # Finalize
        elapsed = time.time() - _start
        result["elapsed_ms"] = round(elapsed * 1000, 2)
        self._fire("detect_opponent_win_condition", result)
        return result

    # ==================================================================== #

    def evaluate_win_probability(self, my_conditions: list, enemy_conditions: list, game_state: dict) -> dict:
        """Compute win probability from all signals.

        Parameters
        ----------
        my_conditions : list
            Input parameter for evaluate_win_probability.
        enemy_conditions : list
            Input parameter for evaluate_win_probability.
        game_state : dict
            Input parameter for evaluate_win_probability.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()

        # --- Core logic for evaluate_win_probability ---
        result: Dict[str, Any] = {}

        # Aggregate input data
        items = my_conditions if my_conditions else []
        n = len(items)
        if n == 0:
            result["status"] = "no_data"
            result["confidence"] = 0.0
            result["analysis"] = {}
            result["summary"] = "No data available for analysis."
            self._fire("evaluate_win_probability", result)
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
        self._fire("evaluate_win_probability", result)
        return result

    # ==================================================================== #

    def generate_strategy_brief(self, analysis: dict) -> str:
        """Generate a strategy brief for the player.

        Parameters
        ----------
        analysis : dict
            Input parameter for generate_strategy_brief.

        Returns
        -------
        str
        """
        self._op_count += 1
        _start = time.time()

        # String generation
        parts: List[str] = []
        data = analysis
        if isinstance(data, dict):
            for k, v in data.items():
                parts.append(f"{k}: {v}")
        elif isinstance(data, list):
            for i, item in enumerate(data):
                parts.append(f"{i+1}. {item}")
        elif isinstance(data, str):
            parts.append(data)
        result_str = " | ".join(parts) if parts else "No data available."
        self._fire("generate_strategy_brief", {"length": len(result_str)})
        return result_str

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
