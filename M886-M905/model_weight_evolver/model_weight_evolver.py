#!/usr/bin/env python3
"""
M901 — ModelWeightEvolver
===========================
Online learning to adjust prediction weights based on feedback data.

Reference: operatorRL agentic self-evolution core
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M901.ModelWeightEvolver")


@dataclass
class WeightUpdate:
    iteration: int
    old_weights: Dict[str, float]
    new_weights: Dict[str, float]
    loss_before: float
    loss_after: float
    learning_rate: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {"iter": self.iteration, "loss_before": round(self.loss_before, 6),
                "loss_after": round(self.loss_after, 6), "lr": self.learning_rate,
                "weight_changes": {k: round(self.new_weights.get(k, 0) - self.old_weights.get(k, 0), 6)
                                   for k in self.old_weights}}


class ModelWeightEvolver:
    """
    Online learning engine for the agentic self-evolution loop.
    Adjusts prediction model weights based on feedback from M900.

    Uses exponential moving average of gradient signals to smoothly
    update weights every N games. This implements the core operatorRL
    principle: self-deployed, self-feedback, self-evolving.
    """

    def __init__(self, feedback_collector=None, win_engine=None,
                 learning_rate: float = 0.01, update_every_n_games: int = 5):
        self._feedback = feedback_collector
        self._win_engine = win_engine
        self._lr = learning_rate
        self._update_interval = update_every_n_games
        self._current_weights: Dict[str, float] = {
            "gold_diff": 0.25, "kill_diff": 0.15, "tower_diff": 0.15,
            "dragon_diff": 0.12, "baron_diff": 0.10, "cs_diff": 0.08,
            "level_diff": 0.08, "comp_score_diff": 0.07,
        }
        self._weight_history: List[WeightUpdate] = []
        self._games_since_update = 0
        self._iteration = 0
        self._ema_gradients: Dict[str, float] = {k: 0.0 for k in self._current_weights}
        self._ema_beta = 0.9
        self._stats = {"updates": 0, "total_games_processed": 0, "best_loss": float("inf")}
        logger.info("ModelWeightEvolver initialized (lr=%.4f, update_every=%d)",
                     learning_rate, update_every_n_games)

    def on_game_complete(self, game_id: str, result: str):
        """Called after each game to potentially trigger weight update."""
        self._games_since_update += 1
        self._stats["total_games_processed"] += 1

        if self._games_since_update >= self._update_interval:
            self._evolve_weights()
            self._games_since_update = 0

    def _evolve_weights(self):
        """Perform one iteration of weight evolution."""
        if not self._feedback:
            return

        training_data = self._feedback.get_training_data()
        if not training_data:
            return

        old_weights = dict(self._current_weights)
        loss_before = self._compute_loss(training_data)

        # Compute gradients from feedback
        gradients = self._compute_gradients(training_data)

        # EMA smoothing
        for key in self._ema_gradients:
            g = gradients.get(key, 0)
            self._ema_gradients[key] = self._ema_beta * self._ema_gradients[key] + (1 - self._ema_beta) * g

        # Update weights
        for key in self._current_weights:
            self._current_weights[key] -= self._lr * self._ema_gradients[key]
            self._current_weights[key] = max(0.01, min(0.5, self._current_weights[key]))

        # Normalize to sum to 1
        total = sum(self._current_weights.values())
        if total > 0:
            for key in self._current_weights:
                self._current_weights[key] /= total

        loss_after = self._compute_loss(training_data)
        self._iteration += 1

        update = WeightUpdate(
            iteration=self._iteration, old_weights=old_weights,
            new_weights=dict(self._current_weights),
            loss_before=loss_before, loss_after=loss_after,
            learning_rate=self._lr,
        )
        self._weight_history.append(update)
        self._stats["updates"] += 1
        self._stats["best_loss"] = min(self._stats["best_loss"], loss_after)

        # Push to win probability engine
        if self._win_engine:
            self._win_engine.update_weights(self._current_weights)

        logger.info("Weight evolution iter %d: loss %.6f → %.6f",
                     self._iteration, loss_before, loss_after)

    def _compute_loss(self, data: List[Dict]) -> float:
        """Simple MSE loss between predicted and actual outcomes."""
        if not data: return 0.0
        total_loss = 0.0
        count = 0
        for entry in data:
            score = entry.get("score", 0)
            total_loss += score ** 2
            count += 1
        return total_loss / max(count, 1)

    def _compute_gradients(self, data: List[Dict]) -> Dict[str, float]:
        """Estimate gradients from feedback data."""
        gradients = {k: 0.0 for k in self._current_weights}
        agg = self._feedback.get_aggregations() if self._feedback else {}

        for cat, stats in agg.items():
            success = stats.get("success_rate", 50)
            adoption = stats.get("adoption_rate", 50)
            signal = (success - 50) / 50  # [-1, 1]

            if cat in gradients:
                gradients[cat] = -signal * 0.1  # negative because we minimize loss

        return gradients

    def get_current_weights(self) -> Dict[str, float]:
        return dict(self._current_weights)

    def get_evolution_history(self) -> List[Dict[str, Any]]:
        return [u.to_dict() for u in self._weight_history[-20:]]

    def export_stats(self) -> Dict[str, Any]:
        return {"evolver_stats": self._stats, "iteration": self._iteration,
                "current_weights": {k: round(v, 4) for k, v in self._current_weights.items()}}



# ---------------------------------------------------------------------------
# Extended ModelWeightEvolver utilities
# ---------------------------------------------------------------------------

class GradientClipping:
    """Clips gradients to prevent explosive weight updates."""

    @staticmethod
    def clip_by_norm(gradients: Dict[str, float], max_norm: float = 1.0) -> Dict[str, float]:
        norm = math.sqrt(sum(g**2 for g in gradients.values()))
        if norm <= max_norm:
            return gradients
        scale = max_norm / norm
        return {k: v * scale for k, v in gradients.items()}

    @staticmethod
    def clip_by_value(gradients: Dict[str, float], max_val: float = 0.1) -> Dict[str, float]:
        return {k: max(-max_val, min(max_val, v)) for k, v in gradients.items()}


class LearningRateScheduler:
    """Adjusts learning rate over time for stable convergence."""

    def __init__(self, initial_lr: float = 0.01, decay: float = 0.995,
                 min_lr: float = 0.001):
        self._initial = initial_lr
        self._decay = decay
        self._min = min_lr
        self._current = initial_lr
        self._step = 0

    def step(self) -> float:
        self._step += 1
        self._current = max(self._min, self._initial * (self._decay ** self._step))
        return self._current

    @property
    def current_lr(self) -> float:
        return self._current

    def reset(self):
        self._step = 0
        self._current = self._initial


class WeightValidator:
    """Validates weight configurations for sanity."""

    @staticmethod
    def validate(weights: Dict[str, float]) -> Tuple[bool, List[str]]:
        errors = []
        for key, val in weights.items():
            if val < 0:
                errors.append(f"Negative weight: {key}={val}")
            if val > 1.0:
                errors.append(f"Weight too large: {key}={val}")
            if math.isnan(val) or math.isinf(val):
                errors.append(f"Invalid weight: {key}={val}")

        total = sum(weights.values())
        if abs(total - 1.0) > 0.01:
            errors.append(f"Weights don't sum to 1.0 (sum={total:.4f})")

        return len(errors) == 0, errors

    @staticmethod
    def normalize(weights: Dict[str, float]) -> Dict[str, float]:
        total = sum(abs(v) for v in weights.values())
        if total == 0:
            n = len(weights)
            return {k: 1.0/n for k in weights}
        return {k: abs(v)/total for k, v in weights.items()}


class WeightSnapshotManager:
    """Manages weight snapshots for rollback and comparison."""

    def __init__(self, max_snapshots: int = 50):
        self._snapshots: List[Dict[str, Any]] = []
        self._max = max_snapshots

    def save(self, weights: Dict[str, float], loss: float, metadata: Optional[Dict] = None):
        snapshot = {
            "weights": dict(weights),
            "loss": loss,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self._max:
            self._snapshots = self._snapshots[-self._max:]

    def get_best(self) -> Optional[Dict[str, Any]]:
        if not self._snapshots:
            return None
        return min(self._snapshots, key=lambda s: s["loss"])

    def rollback_to_best(self) -> Optional[Dict[str, float]]:
        best = self.get_best()
        return best["weights"] if best else None

    def get_loss_history(self) -> List[float]:
        return [s["loss"] for s in self._snapshots]

    def get_weight_drift(self) -> Dict[str, float]:
        """Compute total drift in each weight from first to last snapshot."""
        if len(self._snapshots) < 2:
            return {}
        first = self._snapshots[0]["weights"]
        last = self._snapshots[-1]["weights"]
        return {k: round(last.get(k, 0) - first.get(k, 0), 6) for k in first}


class EarlyStopping:
    """Stops weight evolution when no improvement is detected."""

    def __init__(self, patience: int = 10, min_delta: float = 0.001):
        self._patience = patience
        self._min_delta = min_delta
        self._best_loss = float("inf")
        self._counter = 0

    def should_stop(self, loss: float) -> bool:
        if loss < self._best_loss - self._min_delta:
            self._best_loss = loss
            self._counter = 0
            return False
        self._counter += 1
        return self._counter >= self._patience

    def reset(self):
        self._best_loss = float("inf")
        self._counter = 0


class ABTestManager:
    """Manages A/B testing of different weight configurations."""

    def __init__(self):
        self._variants: Dict[str, Dict[str, float]] = {}
        self._results: Dict[str, List[bool]] = collections.defaultdict(list)

    def add_variant(self, name: str, weights: Dict[str, float]):
        self._variants[name] = weights

    def record_result(self, variant: str, win: bool):
        self._results[variant].append(win)

    def get_results(self) -> Dict[str, Dict[str, Any]]:
        results = {}
        for name, outcomes in self._results.items():
            wins = sum(1 for o in outcomes if o)
            total = len(outcomes)
            results[name] = {
                "wins": wins, "total": total,
                "win_rate": round(wins / max(total, 1) * 100, 1),
            }
        return results

    def get_best_variant(self) -> Optional[str]:
        results = self.get_results()
        if not results:
            return None
        return max(results.items(), key=lambda x: x[1]["win_rate"])[0]



# ---------------------------------------------------------------------------
# Extended ModelWeightEvolver utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class ModelWeightEvolverMetrics:
    """Collects performance metrics for ModelWeightEvolver."""

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


class ModelWeightEvolverSerializer:
    """Serialization utilities for ModelWeightEvolver state."""

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


class ModelWeightEvolverDiagnostics:
    """Diagnostic tools for ModelWeightEvolver troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "ModelWeightEvolver",
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


class ModelWeightEvolverEventLogger:
    """Structured event logger for ModelWeightEvolver with rotation."""

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



class ModelWeightEvolverConfigStore:
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


class ModelWeightEvolverHealthCheck:
    """Periodic health check for the module."""
    def __init__(self, instance):
        self._instance = instance
        self._check_results: List[Dict[str, Any]] = []
        self._consecutive_failures = 0

    def check(self) -> Dict[str, Any]:
        result = {
            "module": "ModelWeightEvolver",
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


class ModelWeightEvolverDataValidator:
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
