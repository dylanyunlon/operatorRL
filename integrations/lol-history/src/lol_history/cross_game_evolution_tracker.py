"""
CrossGameEvolutionTracker — Track model evolution across games, detect transfer effects.

Monitors model performance trends across multiple games and identifies positive
or negative transfer learning effects.

Location: integrations/lol-history/src/lol_history/cross_game_evolution_tracker.py

Reference (拿来主義):
  - agentos/governance/fitness_aggregator.py: get_trend analysis
  - integrations/lol-history/src/lol_history/coaching_effectiveness_tracker.py（M613）:
    effectiveness evaluation
  - DI-star: multi-agent evolution tracking

Design Notes (Knuth-level critique):
  User:
    - record_performance() adds a data point per (game, model, metric).
    - detect_transfer_effect() identifies positive/negative transfer between games.
    - get_evolution_summary() provides cross-game dashboard data.
  System:
    - Per-game per-model time series for trend analysis.
    - Transfer detection via before/after comparison of performance after transfer event.
    - Exponential decay weighting for recency bias in trend analysis.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.cross_game_evolution_tracker.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class _TimeSeries:
    """Simple time series with trend analysis."""

    def __init__(self, max_size: int = 500) -> None:
        self._data: List[Tuple[float, float]] = []  # (ts, value)
        self._max_size = max_size

    def add(self, value: float, ts: Optional[float] = None) -> None:
        ts = ts or time.time()
        self._data.append((ts, value))
        if len(self._data) > self._max_size:
            self._data = self._data[-self._max_size:]

    @property
    def count(self) -> int:
        return len(self._data)

    @property
    def latest(self) -> Optional[float]:
        return self._data[-1][1] if self._data else None

    def get_trend(self, window: int = 10) -> str:
        """Return 'improving', 'declining', or 'stable'."""
        if len(self._data) < 4:
            return "insufficient_data"
        recent = [v for _, v in self._data[-window:]]
        older = [v for _, v in self._data[-2 * window:-window]]
        if not older:
            return "insufficient_data"
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        diff = recent_avg - older_avg
        threshold = 0.05 * max(abs(older_avg), 0.01)
        if diff > threshold:
            return "improving"
        elif diff < -threshold:
            return "declining"
        return "stable"

    def get_values_after(self, ts: float) -> List[float]:
        return [v for t, v in self._data if t >= ts]

    def get_values_before(self, ts: float) -> List[float]:
        return [v for t, v in self._data if t < ts]

    def to_summary(self) -> Dict[str, Any]:
        if not self._data:
            return {"count": 0}
        vals = [v for _, v in self._data]
        return {
            "count": len(vals),
            "latest": vals[-1],
            "mean": sum(vals) / len(vals),
            "min": min(vals),
            "max": max(vals),
            "trend": self.get_trend(),
        }


class CrossGameEvolutionTracker:
    """Track model evolution across multiple games.

    Public API:
        record_performance(game_type, model_name, metric, value)
        record_transfer_event(source_game, target_game, model_name)
        detect_transfer_effect(game_type, model_name, metric) -> dict
        get_evolution_summary(game_type=None) -> dict
        get_trend(game_type, model_name, metric) -> str
        get_stats() -> dict
    """

    def __init__(self) -> None:
        # (game_type, model_name, metric) → _TimeSeries
        self._series: Dict[Tuple[str, str, str], _TimeSeries] = {}
        # List of transfer events
        self._transfer_events: List[Dict[str, Any]] = []
        self._record_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def _key(self, game: str, model: str, metric: str) -> Tuple[str, str, str]:
        return (game, model, metric)

    def record_performance(
        self, game_type: str, model_name: str, metric: str, value: float,
    ) -> None:
        key = self._key(game_type, model_name, metric)
        if key not in self._series:
            self._series[key] = _TimeSeries()
        self._series[key].add(value)
        self._record_count += 1

    def record_transfer_event(
        self, source_game: str, target_game: str, model_name: str,
    ) -> None:
        self._transfer_events.append({
            "source_game": source_game,
            "target_game": target_game,
            "model_name": model_name,
            "ts": time.time(),
        })
        self._fire("transfer_event", {
            "source": source_game, "target": target_game, "model": model_name,
        })

    def detect_transfer_effect(
        self, game_type: str, model_name: str, metric: str,
    ) -> Dict[str, Any]:
        """Detect whether recent transfer had positive/negative effect."""
        # Find most recent transfer event involving this game/model
        relevant = [
            e for e in self._transfer_events
            if e["target_game"] == game_type and e["model_name"] == model_name
        ]
        if not relevant:
            return {"effect": "no_transfer_found"}

        latest_transfer = relevant[-1]
        ts = latest_transfer["ts"]

        key = self._key(game_type, model_name, metric)
        series = self._series.get(key)
        if series is None or series.count < 4:
            return {"effect": "insufficient_data"}

        before = series.get_values_before(ts)
        after = series.get_values_after(ts)

        if len(before) < 2 or len(after) < 2:
            return {"effect": "insufficient_data"}

        before_avg = sum(before[-10:]) / min(len(before), 10)
        after_avg = sum(after) / len(after)
        delta = after_avg - before_avg
        threshold = 0.05 * max(abs(before_avg), 0.01)

        if delta > threshold:
            effect = "positive_transfer"
        elif delta < -threshold:
            effect = "negative_transfer"
        else:
            effect = "neutral"

        return {
            "effect": effect,
            "before_avg": round(before_avg, 4),
            "after_avg": round(after_avg, 4),
            "delta": round(delta, 4),
            "source_game": latest_transfer["source_game"],
            "transfer_ts": ts,
        }

    def get_trend(self, game_type: str, model_name: str, metric: str) -> str:
        key = self._key(game_type, model_name, metric)
        series = self._series.get(key)
        return series.get_trend() if series else "no_data"

    def get_evolution_summary(self, game_type: Optional[str] = None) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        for (gt, mn, met), series in self._series.items():
            if game_type and gt != game_type:
                continue
            if gt not in summary:
                summary[gt] = {}
            if mn not in summary[gt]:
                summary[gt][mn] = {}
            summary[gt][mn][met] = series.to_summary()
        return {
            "games": summary,
            "total_records": self._record_count,
            "transfer_events": len(self._transfer_events),
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "record_count": self._record_count,
            "series_count": len(self._series),
            "transfer_events": len(self._transfer_events),
            "games": list(set(k[0] for k in self._series.keys())),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")
