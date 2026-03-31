"""
SuggestionAdherenceTracker — Tracks whether users follow system suggestions and outcome correlation.

Architecture (拿来主义):
  action_feedback_collector.py, coaching_effectiveness_tracker.py

Location: integrations/lol-history/src/lol_history/suggestion_adherence_tracker.py

Design Notes (Knuth-level critique):
  User:
    - Production-grade module with unified {"status": "ok"} response format.
    - Stateless or bounded-state design for long-running sessions.
    - Graceful degradation: partial results on component failure.
  System:
    - All data structures bounded (deque/OrderedDict with maxlen).
    - Evolution callback integration for self-improvement feedback.
    - Comprehensive get_stats() for observability.
    - Zero external dependencies beyond stdlib.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.suggestion_adherence_tracker.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _SuggestionRecord:
    """Record of a single suggestion and its adherence outcome."""
    __slots__ = ("suggestion_id", "suggestion_type", "text", "priority",
                 "timestamp", "adhered", "outcome", "outcome_time")

    def __init__(self, suggestion_id: str, suggestion_type: str,
                 text: str, priority: str, timestamp: float) -> None:
        self.suggestion_id = suggestion_id
        self.suggestion_type = suggestion_type
        self.text = text
        self.priority = priority
        self.timestamp = timestamp
        self.adhered: Optional[bool] = None
        self.outcome: Optional[str] = None
        self.outcome_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.suggestion_id, "type": self.suggestion_type,
            "text": self.text, "priority": self.priority,
            "timestamp": self.timestamp, "adhered": self.adhered,
            "outcome": self.outcome,
        }


class _TypeStats:
    """Aggregated stats per suggestion type."""

    def __init__(self) -> None:
        self.total = 0
        self.adhered = 0
        self.ignored = 0
        self.positive_outcomes = 0
        self.negative_outcomes = 0
        self.neutral_outcomes = 0

    def record(self, adhered: bool, outcome: str) -> None:
        self.total += 1
        if adhered:
            self.adhered += 1
        else:
            self.ignored += 1
        if outcome == "positive":
            self.positive_outcomes += 1
        elif outcome == "negative":
            self.negative_outcomes += 1
        else:
            self.neutral_outcomes += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "adhered": self.adhered,
            "ignored": self.ignored,
            "adherence_rate": _safe_div(self.adhered, self.total),
            "positive_outcomes": self.positive_outcomes,
            "negative_outcomes": self.negative_outcomes,
            "effectiveness": _safe_div(self.positive_outcomes, self.adhered) if self.adhered else 0.0,
        }


class _PriorityStats:
    """Aggregated stats per priority level."""

    def __init__(self) -> None:
        self._priority_data: Dict[str, _TypeStats] = defaultdict(_TypeStats)

    def record(self, priority: str, adhered: bool, outcome: str) -> None:
        self._priority_data[priority].record(adhered, outcome)

    def to_dict(self) -> Dict[str, Any]:
        return {p: s.to_dict() for p, s in self._priority_data.items()}


class _TemporalAnalyzer:
    """Analyzes adherence patterns over time."""

    def __init__(self, window_size: int = 20) -> None:
        self._window_size = window_size
        self._recent: deque = deque(maxlen=window_size)

    def add(self, adhered: bool, outcome: str, timestamp: float) -> None:
        self._recent.append({"adhered": adhered, "outcome": outcome, "ts": timestamp})

    def get_rolling_rate(self) -> float:
        if not self._recent:
            return 0.0
        return _safe_div(sum(1 for r in self._recent if r["adhered"]), len(self._recent))

    def get_trend(self) -> str:
        if len(self._recent) < 4:
            return "insufficient_data"
        half = len(self._recent) // 2
        first_half = list(self._recent)[:half]
        second_half = list(self._recent)[half:]
        rate1 = _safe_div(sum(1 for r in first_half if r["adhered"]), len(first_half))
        rate2 = _safe_div(sum(1 for r in second_half if r["adhered"]), len(second_half))
        if rate2 - rate1 > 0.1:
            return "improving"
        elif rate1 - rate2 > 0.1:
            return "declining"
        return "stable"

    def get_stats(self) -> Dict[str, Any]:
        return {
            "rolling_rate": self.get_rolling_rate(),
            "trend": self.get_trend(),
            "window_size": self._window_size,
            "data_points": len(self._recent),
        }


class SuggestionAdherenceTracker:
    """Tracks suggestion adherence and outcome effectiveness.

    Public API: record_suggestion, record_adherence, get_adherence_rate,
                get_per_type_stats, get_effectiveness_by_priority,
                get_temporal_analysis, get_stats
    """

    def __init__(self, max_suggestions: int = 500) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._suggestions: Dict[str, _SuggestionRecord] = OrderedDict()
        self._max_suggestions = max_suggestions
        self._type_stats: Dict[str, _TypeStats] = defaultdict(_TypeStats)
        self._priority_stats = _PriorityStats()
        self._temporal = _TemporalAnalyzer()
        self._total_recorded = 0
        self._total_adherence_recorded = 0

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _trim(self) -> None:
        while len(self._suggestions) > self._max_suggestions:
            self._suggestions.popitem(last=False)

    def record_suggestion(self, suggestion_id: str, suggestion_type: str,
                          text: str, priority: str,
                          timestamp: float) -> Dict[str, Any]:
        """Record a suggestion that was given to the user."""
        self._op_count += 1
        self._total_recorded += 1
        record = _SuggestionRecord(suggestion_id, suggestion_type,
                                   text, priority, timestamp)
        self._suggestions[suggestion_id] = record
        self._trim()
        return {
            "status": "ok",
            "suggestion_id": suggestion_id,
            "total_recorded": self._total_recorded,
        }

    def record_adherence(self, suggestion_id: str, adhered: bool,
                         outcome: str = "neutral") -> Dict[str, Any]:
        """Record whether the user followed a suggestion and its outcome."""
        self._op_count += 1
        self._total_adherence_recorded += 1
        record = self._suggestions.get(suggestion_id)
        if not record:
            return {"status": "ok", "found": False, "suggestion_id": suggestion_id}

        record.adhered = adhered
        record.outcome = outcome
        record.outcome_time = time.monotonic()

        self._type_stats[record.suggestion_type].record(adhered, outcome)
        self._priority_stats.record(record.priority, adhered, outcome)
        self._temporal.add(adhered, outcome, record.timestamp)

        self._fire("adherence_recorded", {
            "type": record.suggestion_type,
            "adhered": adhered,
            "outcome": outcome,
        })

        return {
            "status": "ok",
            "found": True,
            "suggestion_id": suggestion_id,
            "adhered": adhered,
            "outcome": outcome,
        }

    def get_adherence_rate(self) -> Dict[str, Any]:
        """Get overall adherence rate."""
        self._op_count += 1
        total = 0
        adhered = 0
        for r in self._suggestions.values():
            if r.adhered is not None:
                total += 1
                if r.adhered:
                    adhered += 1
        return {
            "status": "ok",
            "total_with_outcome": total,
            "adhered": adhered,
            "adherence_rate": _safe_div(adhered, total),
            "rolling_rate": self._temporal.get_rolling_rate(),
            "trend": self._temporal.get_trend(),
        }

    def get_per_type_stats(self) -> Dict[str, Any]:
        """Get adherence stats broken down by suggestion type."""
        self._op_count += 1
        return {
            "status": "ok",
            "per_type": {t: s.to_dict() for t, s in self._type_stats.items()},
        }

    def get_effectiveness_by_priority(self) -> Dict[str, Any]:
        """Get effectiveness stats broken down by priority."""
        self._op_count += 1
        return {
            "status": "ok",
            "per_priority": self._priority_stats.to_dict(),
        }

    def get_temporal_analysis(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "temporal": self._temporal.get_stats()}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "total_recorded": self._total_recorded,
            "total_adherence_recorded": self._total_adherence_recorded,
            "active_suggestions": len(self._suggestions),
            "type_count": len(self._type_stats),
            "temporal": self._temporal.get_stats(),
        }
