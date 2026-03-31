"""
EvolutionFeedbackSignalRouter — Routes evolution feedback signals to module handlers.

Architecture (拿来主义):
  history_evolution_bridge.py — evolution bridging patterns
  intel_reward_signal_generator.py（M728）— reward signal generation

Location: integrations/lol-history/src/lol_history/evolution_feedback_signal_router.py

Design Notes (Knuth-level critique):
  User:
    - Signal types: prediction_accuracy, suggestion_adherence, winrate_change, latency_sla.
    - Each signal routes to registered handlers for that signal type.
    - Batch mode: signals accumulate and flush periodically to reduce overhead.
  System:
    - Priority queue ensures critical signals (winrate drop) process before info signals.
    - Throttle per signal type: max N signals per minute to prevent feedback storms.
    - Dead-letter queue for failed deliveries with retry logic.
    - Signal deduplication within configurable time window.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.evolution_feedback_signal_router.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _SignalPriority:
    """Signal priority classification."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3

    TYPE_PRIORITIES = {
        "winrate_drop": CRITICAL,
        "model_degradation": CRITICAL,
        "prediction_accuracy": HIGH,
        "suggestion_adherence": NORMAL,
        "winrate_change": NORMAL,
        "latency_sla": HIGH,
        "data_quality": NORMAL,
        "module_health": LOW,
    }

    @classmethod
    def get(cls, signal_type: str) -> int:
        return cls.TYPE_PRIORITIES.get(signal_type, cls.NORMAL)


class _ThrottleController:
    """Per-type signal throttling to prevent feedback storms."""

    def __init__(self, max_per_minute: int = 10) -> None:
        self._max_per_minute = max_per_minute
        self._type_timestamps: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._throttled_count = 0

    def should_throttle(self, signal_type: str) -> bool:
        now = time.monotonic()
        ts_deque = self._type_timestamps[signal_type]
        while ts_deque and now - ts_deque[0] > 60.0:
            ts_deque.popleft()
        if len(ts_deque) >= self._max_per_minute:
            self._throttled_count += 1
            return True
        ts_deque.append(now)
        return False

    def get_stats(self) -> Dict[str, Any]:
        return {
            "max_per_minute": self._max_per_minute,
            "throttled_count": self._throttled_count,
            "active_types": len(self._type_timestamps),
        }


class _SignalDeduplicator:
    """Deduplicates signals within a time window."""

    def __init__(self, window_seconds: float = 5.0) -> None:
        self._window = window_seconds
        self._seen: Dict[str, float] = {}
        self._dedup_count = 0

    def is_duplicate(self, signal_hash: str) -> bool:
        now = time.monotonic()
        expired = [k for k, t in self._seen.items() if now - t > self._window]
        for k in expired:
            del self._seen[k]
        if signal_hash in self._seen:
            self._dedup_count += 1
            return True
        self._seen[signal_hash] = now
        return False

    def compute_hash(self, signal_type: str, data: Dict) -> str:
        import hashlib, json
        content = f"{signal_type}:{json.dumps(data, sort_keys=True, default=str)}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "window_seconds": self._window,
            "tracked_hashes": len(self._seen),
            "dedup_count": self._dedup_count,
        }


class _HandlerRegistry:
    """Registry of signal handlers by signal type."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._global_handlers: List[Callable] = []
        self._dispatch_count = 0
        self._error_count = 0

    def register(self, signal_type: str, handler: Callable) -> int:
        self._handlers[signal_type].append(handler)
        return len(self._handlers[signal_type])

    def register_global(self, handler: Callable) -> int:
        self._global_handlers.append(handler)
        return len(self._global_handlers)

    def dispatch(self, signal_type: str, signal: Dict[str, Any]) -> Tuple[int, List[str]]:
        dispatched = 0
        errors = []
        for handler in self._handlers.get(signal_type, []):
            try:
                handler(signal)
                dispatched += 1
            except Exception as e:
                self._error_count += 1
                errors.append(f"{signal_type}: {e}")
        for handler in self._global_handlers:
            try:
                handler(signal)
                dispatched += 1
            except Exception as e:
                self._error_count += 1
                errors.append(f"global: {e}")
        self._dispatch_count += dispatched
        return dispatched, errors

    def get_stats(self) -> Dict[str, Any]:
        return {
            "type_handlers": {k: len(v) for k, v in self._handlers.items()},
            "global_handlers": len(self._global_handlers),
            "dispatch_count": self._dispatch_count,
            "error_count": self._error_count,
        }


class _BatchBuffer:
    """Accumulates signals for batch processing."""

    def __init__(self, max_batch_size: int = 50,
                 flush_interval: float = 10.0) -> None:
        self._buffer: List[Dict[str, Any]] = []
        self._max_size = max_batch_size
        self._flush_interval = flush_interval
        self._last_flush = time.monotonic()
        self._flush_count = 0
        self._total_buffered = 0

    def add(self, signal: Dict[str, Any]) -> bool:
        self._total_buffered += 1
        self._buffer.append(signal)
        return len(self._buffer) >= self._max_size

    def should_flush(self) -> bool:
        if len(self._buffer) >= self._max_size:
            return True
        if time.monotonic() - self._last_flush >= self._flush_interval:
            return True
        return False

    def flush(self) -> List[Dict[str, Any]]:
        batch = list(self._buffer)
        self._buffer.clear()
        self._last_flush = time.monotonic()
        self._flush_count += 1
        return batch

    def size(self) -> int:
        return len(self._buffer)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "buffer_size": len(self._buffer),
            "max_size": self._max_size,
            "flush_interval": self._flush_interval,
            "flush_count": self._flush_count,
            "total_buffered": self._total_buffered,
        }


class _DeadLetterQueue:
    """Stores failed signal deliveries for retry."""

    def __init__(self, max_size: int = 100, max_retries: int = 3) -> None:
        self._queue: deque = deque(maxlen=max_size)
        self._max_retries = max_retries
        self._retry_count = 0

    def add(self, signal: Dict[str, Any], errors: List[str]) -> None:
        retries = signal.get("_retry_count", 0)
        if retries < self._max_retries:
            signal["_retry_count"] = retries + 1
            signal["_last_errors"] = errors
            self._queue.append(signal)

    def get_pending(self, limit: int = 10) -> List[Dict]:
        return list(self._queue)[:limit]

    def pop(self) -> Optional[Dict]:
        if self._queue:
            self._retry_count += 1
            return self._queue.popleft()
        return None

    def get_stats(self) -> Dict[str, Any]:
        return {
            "pending": len(self._queue),
            "retry_count": self._retry_count,
            "max_retries": self._max_retries,
        }


class _SignalAnalytics:
    """Tracks signal routing analytics."""

    def __init__(self, max_history: int = 500) -> None:
        self._history: deque = deque(maxlen=max_history)
        self._type_counts: Dict[str, int] = defaultdict(int)
        self._priority_counts: Dict[int, int] = defaultdict(int)

    def record(self, signal_type: str, priority: int,
               dispatched: int, errors: int) -> None:
        self._history.append({
            "ts": time.monotonic(),
            "type": signal_type,
            "priority": priority,
            "dispatched": dispatched,
            "errors": errors,
        })
        self._type_counts[signal_type] += 1
        self._priority_counts[priority] += 1

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_signals": len(self._history),
            "type_counts": dict(self._type_counts),
            "priority_counts": dict(self._priority_counts),
        }


class EvolutionFeedbackSignalRouter:
    """Routes evolution feedback signals with priority, throttling, and batch support.

    Public API: route_signal, register_handler, register_global_handler,
                flush_batch, retry_dead_letters, get_analytics, get_stats
    """

    def __init__(self, batch_mode: bool = False,
                 max_per_minute: int = 10) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._route_count = 0
        self._batch_mode = batch_mode
        self._throttle = _ThrottleController(max_per_minute=max_per_minute)
        self._dedup = _SignalDeduplicator()
        self._registry = _HandlerRegistry()
        self._batch = _BatchBuffer()
        self._dlq = _DeadLetterQueue()
        self._analytics = _SignalAnalytics()

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def route_signal(self, signal_type: str,
                     data: Dict[str, Any]) -> Dict[str, Any]:
        """Route an evolution feedback signal to registered handlers."""
        self._op_count += 1
        self._route_count += 1
        priority = _SignalPriority.get(signal_type)

        sig_hash = self._dedup.compute_hash(signal_type, data)
        if self._dedup.is_duplicate(sig_hash):
            return {"status": "ok", "action": "deduplicated", "signal_type": signal_type}

        if self._throttle.should_throttle(signal_type):
            return {"status": "ok", "action": "throttled", "signal_type": signal_type}

        signal = {
            "signal_type": signal_type,
            "priority": priority,
            "data": data,
            "timestamp": time.monotonic(),
            "route_num": self._route_count,
        }

        if self._batch_mode and priority > _SignalPriority.HIGH:
            full = self._batch.add(signal)
            if full or self._batch.should_flush():
                return self.flush_batch()
            return {"status": "ok", "action": "buffered", "buffer_size": self._batch.size()}

        dispatched, errors = self._registry.dispatch(signal_type, signal)
        if errors:
            self._dlq.add(signal, errors)

        self._analytics.record(signal_type, priority, dispatched, len(errors))
        self._fire("signal_routed", {"type": signal_type, "dispatched": dispatched})

        return {
            "status": "ok",
            "action": "dispatched",
            "signal_type": signal_type,
            "priority": priority,
            "dispatched": dispatched,
            "errors": errors,
        }

    def register_handler(self, signal_type: str,
                         handler: Callable) -> Dict[str, Any]:
        self._op_count += 1
        count = self._registry.register(signal_type, handler)
        return {"status": "ok", "signal_type": signal_type, "handler_count": count}

    def register_global_handler(self, handler: Callable) -> Dict[str, Any]:
        self._op_count += 1
        count = self._registry.register_global(handler)
        return {"status": "ok", "global_handler_count": count}

    def flush_batch(self) -> Dict[str, Any]:
        """Flush all buffered signals."""
        self._op_count += 1
        batch = self._batch.flush()
        total_dispatched = 0
        total_errors = []

        batch.sort(key=lambda s: s.get("priority", 2))
        for signal in batch:
            d, e = self._registry.dispatch(signal["signal_type"], signal)
            total_dispatched += d
            total_errors.extend(e)
            if e:
                self._dlq.add(signal, e)
            self._analytics.record(signal["signal_type"], signal.get("priority", 2), d, len(e))

        return {
            "status": "ok",
            "action": "batch_flushed",
            "batch_size": len(batch),
            "dispatched": total_dispatched,
            "errors": total_errors,
        }

    def retry_dead_letters(self, limit: int = 5) -> Dict[str, Any]:
        """Retry failed signal deliveries."""
        self._op_count += 1
        retried = 0
        for _ in range(limit):
            signal = self._dlq.pop()
            if not signal:
                break
            d, e = self._registry.dispatch(signal["signal_type"], signal)
            if e:
                self._dlq.add(signal, e)
            retried += 1
        return {"status": "ok", "retried": retried, "dlq_remaining": len(self._dlq._queue)}

    def get_analytics(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "analytics": self._analytics.get_summary()}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "route_count": self._route_count,
            "batch_mode": self._batch_mode,
            "throttle": self._throttle.get_stats(),
            "dedup": self._dedup.get_stats(),
            "registry": self._registry.get_stats(),
            "batch": self._batch.get_stats(),
            "dlq": self._dlq.get_stats(),
            "analytics": self._analytics.get_summary(),
        }
