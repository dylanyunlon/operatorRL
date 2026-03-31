#!/usr/bin/env python3
"""
M900 — AgenticFeedbackCollector
=================================
Collects user adoption rates of AI suggestions for self-evolution loop.

Reference: M866-M885 strategy_feedback_loop pattern
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M900.AgenticFeedbackCollector")


class FeedbackType(Enum):
    ADVICE_ADOPTED = "adopted"
    ADVICE_IGNORED = "ignored"
    ADVICE_PARTIALLY = "partial"
    OUTCOME_POSITIVE = "positive"
    OUTCOME_NEGATIVE = "negative"
    USER_EXPLICIT = "explicit"


@dataclass
class FeedbackEntry:
    feedback_id: str
    game_id: str
    game_time: float
    advice_category: str
    advice_text: str
    feedback_type: FeedbackType
    outcome_score: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.feedback_id, "game": self.game_id,
                "time": self.game_time, "category": self.advice_category,
                "type": self.feedback_type.value, "score": self.outcome_score}


@dataclass
class FeedbackAggregation:
    category: str
    total_entries: int = 0
    adopted: int = 0
    ignored: int = 0
    positive_outcomes: int = 0
    negative_outcomes: int = 0
    avg_outcome_score: float = 0.0

    @property
    def adoption_rate(self) -> float:
        return (self.adopted / max(self.total_entries, 1)) * 100

    @property
    def success_rate(self) -> float:
        total = self.positive_outcomes + self.negative_outcomes
        return (self.positive_outcomes / max(total, 1)) * 100

    def to_dict(self) -> Dict[str, Any]:
        return {"category": self.category, "total": self.total_entries,
                "adoption_rate": round(self.adoption_rate, 1),
                "success_rate": round(self.success_rate, 1),
                "avg_score": round(self.avg_outcome_score, 3)}


class AgenticFeedbackCollector:
    """
    Core of the agentic self-evolution loop.
    Collects feedback on AI advice adoption and game outcomes,
    feeding M901 ModelWeightEvolver for online learning.
    """

    def __init__(self):
        self._entries: List[FeedbackEntry] = []
        self._aggregations: Dict[str, FeedbackAggregation] = {}
        self._game_outcomes: Dict[str, str] = {}
        self._stats = {"entries_collected": 0, "games_tracked": 0}
        logger.info("AgenticFeedbackCollector initialized")

    def record(self, game_id: str, game_time: float, category: str,
               advice: str, feedback_type: FeedbackType, outcome_score: float = 0.0):
        fid = hashlib.md5(f"{game_id}-{game_time}-{category}".encode()).hexdigest()[:12]
        entry = FeedbackEntry(
            feedback_id=fid, game_id=game_id, game_time=game_time,
            advice_category=category, advice_text=advice,
            feedback_type=feedback_type, outcome_score=outcome_score,
        )
        self._entries.append(entry)
        self._stats["entries_collected"] += 1
        self._update_aggregation(entry)

    def record_game_outcome(self, game_id: str, result: str):
        self._game_outcomes[game_id] = result
        self._stats["games_tracked"] += 1

    def _update_aggregation(self, entry: FeedbackEntry):
        cat = entry.advice_category
        if cat not in self._aggregations:
            self._aggregations[cat] = FeedbackAggregation(category=cat)
        agg = self._aggregations[cat]
        agg.total_entries += 1
        if entry.feedback_type == FeedbackType.ADVICE_ADOPTED:
            agg.adopted += 1
        elif entry.feedback_type == FeedbackType.ADVICE_IGNORED:
            agg.ignored += 1
        if entry.feedback_type == FeedbackType.OUTCOME_POSITIVE:
            agg.positive_outcomes += 1
        elif entry.feedback_type == FeedbackType.OUTCOME_NEGATIVE:
            agg.negative_outcomes += 1
        # Running average
        n = agg.total_entries
        agg.avg_outcome_score = agg.avg_outcome_score * (n-1)/n + entry.outcome_score / n

    def get_aggregations(self) -> Dict[str, Dict[str, Any]]:
        return {k: v.to_dict() for k, v in self._aggregations.items()}

    def get_training_data(self) -> List[Dict[str, Any]]:
        """Export data for M901 ModelWeightEvolver."""
        return [e.to_dict() for e in self._entries]

    def get_category_performance(self, category: str) -> Optional[Dict[str, Any]]:
        agg = self._aggregations.get(category)
        return agg.to_dict() if agg else None

    def export_stats(self) -> Dict[str, Any]:
        return {"collector_stats": self._stats,
                "categories": list(self._aggregations.keys()),
                "total_entries": len(self._entries)}



# ---------------------------------------------------------------------------
# Extended AgenticFeedbackCollector utilities
# ---------------------------------------------------------------------------

class FeedbackCorrelator:
    """Correlates advice with game outcomes for learning signals."""

    def __init__(self):
        self._advice_outcomes: List[Dict[str, Any]] = []

    def correlate(self, advice_time: float, advice_category: str,
                  outcome_time: float, outcome_positive: bool,
                  game_id: str):
        """Record correlation between advice and subsequent outcome."""
        delay = outcome_time - advice_time
        self._advice_outcomes.append({
            "game_id": game_id,
            "category": advice_category,
            "advice_time": advice_time,
            "outcome_time": outcome_time,
            "delay_seconds": delay,
            "positive": outcome_positive,
        })

    def get_category_effectiveness(self) -> Dict[str, Dict[str, Any]]:
        categories: Dict[str, List[bool]] = collections.defaultdict(list)
        for entry in self._advice_outcomes:
            categories[entry["category"]].append(entry["positive"])

        result = {}
        for cat, outcomes in categories.items():
            total = len(outcomes)
            positive = sum(1 for o in outcomes if o)
            result[cat] = {
                "total": total,
                "positive": positive,
                "effectiveness": round(positive / max(total, 1) * 100, 1),
            }
        return result

    def get_best_categories(self, top_n: int = 5) -> List[Tuple[str, float]]:
        effectiveness = self.get_category_effectiveness()
        sorted_cats = sorted(
            effectiveness.items(),
            key=lambda x: x[1]["effectiveness"],
            reverse=True,
        )
        return [(cat, data["effectiveness"]) for cat, data in sorted_cats[:top_n]]


class UserPreferenceTracker:
    """Tracks which types of advice the user responds to positively."""

    def __init__(self):
        self._preferences: Dict[str, float] = {}
        self._interaction_counts: Dict[str, int] = collections.defaultdict(int)
        self._decay_factor = 0.95

    def record_interaction(self, category: str, adopted: bool):
        self._interaction_counts[category] += 1
        current = self._preferences.get(category, 0.5)
        signal = 1.0 if adopted else 0.0
        # Exponential moving average
        alpha = min(0.3, 1.0 / self._interaction_counts[category])
        self._preferences[category] = current * (1 - alpha) + signal * alpha

    def get_preference_score(self, category: str) -> float:
        return self._preferences.get(category, 0.5)

    def should_give_advice(self, category: str) -> bool:
        """Return True if user historically responds well to this category."""
        return self.get_preference_score(category) >= 0.3

    def get_all_preferences(self) -> Dict[str, float]:
        return {k: round(v, 3) for k, v in sorted(
            self._preferences.items(), key=lambda x: x[1], reverse=True
        )}


class FeedbackBatchProcessor:
    """Processes feedback entries in batches for efficiency."""

    def __init__(self, collector):
        self._collector = collector
        self._batch: List[Dict[str, Any]] = []
        self._batch_size = 50

    def add(self, entry: Dict[str, Any]):
        self._batch.append(entry)
        if len(self._batch) >= self._batch_size:
            self.flush()

    def flush(self):
        """Process all pending feedback entries."""
        for entry in self._batch:
            self._collector.record(
                game_id=entry.get("game_id", ""),
                game_time=entry.get("game_time", 0),
                category=entry.get("category", ""),
                advice=entry.get("advice", ""),
                feedback_type=FeedbackType(entry.get("type", "adopted")),
                outcome_score=entry.get("score", 0),
            )
        self._batch.clear()

    @property
    def pending(self) -> int:
        return len(self._batch)


class FeedbackReporter:
    """Generates feedback reports for debugging and analysis."""

    @staticmethod
    def generate_summary(aggregations: Dict[str, Dict]) -> str:
        lines = ["=== Feedback Summary ==="]
        for cat, data in sorted(aggregations.items()):
            lines.append(
                f"  {cat}: adoption={data.get('adoption_rate', 0):.0f}%, "
                f"success={data.get('success_rate', 0):.0f}%, "
                f"n={data.get('total', 0)}"
            )
        return "\n".join(lines)

    @staticmethod
    def identify_weak_categories(aggregations: Dict[str, Dict],
                                  threshold: float = 40.0) -> List[str]:
        weak = []
        for cat, data in aggregations.items():
            if data.get("success_rate", 100) < threshold:
                weak.append(cat)
        return weak



# ---------------------------------------------------------------------------
# Extended AgenticFeedbackCollector utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class AgenticFeedbackCollectorMetrics:
    """Collects performance metrics for AgenticFeedbackCollector."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class AgenticFeedbackCollectorSerializer:
    """Serialization utilities for AgenticFeedbackCollector state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class AgenticFeedbackCollectorDiagnostics:
    """Diagnostic tools for AgenticFeedbackCollector troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "AgenticFeedbackCollector",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class AgenticFeedbackCollectorEventLogger:
    """Structured event logger for AgenticFeedbackCollector with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)



class AgenticFeedbackCollectorConfigStore:
    """Configuration store for runtime settings."""
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._defaults: Dict[str, Any] = {}
        self._change_log: List[Dict[str, Any]] = []

    def set_default(self, key: str, value: Any):
        self._defaults[key] = value
        if key not in self._config:
            self._config[key] = value

    def get(self, key: str, fallback: Any = None) -> Any:
        return self._config.get(key, self._defaults.get(key, fallback))

    def set(self, key: str, value: Any):
        old = self._config.get(key)
        self._config[key] = value
        self._change_log.append({
            "key": key, "old": old, "new": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def reset_to_defaults(self):
        self._config = dict(self._defaults)

    def get_all(self) -> Dict[str, Any]:
        merged = dict(self._defaults)
        merged.update(self._config)
        return merged

    def get_changes(self) -> List[Dict[str, Any]]:
        return list(self._change_log)


class AgenticFeedbackCollectorHealthCheck:
    """Periodic health check for the module."""
    def __init__(self, instance):
        self._instance = instance
        self._check_results: List[Dict[str, Any]] = []
        self._consecutive_failures = 0

    def check(self) -> Dict[str, Any]:
        result = {
            "module": "AgenticFeedbackCollector",
            "healthy": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }
        # Verify instance is responsive
        try:
            if hasattr(self._instance, "export_stats"):
                stats = self._instance.export_stats()
                result["checks"].append({"name": "export_stats", "ok": True})
            self._consecutive_failures = 0
        except Exception as exc:
            result["healthy"] = False
            result["checks"].append({"name": "export_stats", "ok": False, "error": str(exc)})
            self._consecutive_failures += 1

        result["consecutive_failures"] = self._consecutive_failures
        self._check_results.append(result)
        if len(self._check_results) > 100:
            self._check_results = self._check_results[-100:]
        return result

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._check_results)

    @property
    def is_healthy(self) -> bool:
        if not self._check_results:
            return True
        return self._check_results[-1].get("healthy", False)


class AgenticFeedbackCollectorDataValidator:
    """Validates input and output data for the module."""

    @staticmethod
    def validate_dict(data: Dict[str, Any], required_keys: List[str]) -> Tuple[bool, List[str]]:
        errors = []
        for key in required_keys:
            if key not in data:
                errors.append(f"Missing required key: {key}")
        return len(errors) == 0, errors

    @staticmethod
    def validate_numeric_range(value: float, min_val: float, max_val: float,
                                field_name: str = "value") -> Tuple[bool, str]:
        if value < min_val or value > max_val:
            return False, f"{field_name} {value} outside range [{min_val}, {max_val}]"
        return True, ""

    @staticmethod
    def sanitize_string(s: str, max_length: int = 256) -> str:
        return s[:max_length].strip()
