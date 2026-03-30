"""
Health Checker — Deployment health monitoring with multi-signal checks.

Monitors deployed model health across multiple signals: error rate,
latency, prediction quality, resource usage. Computes composite health
score and triggers alerts on degradation.

Location: agentlightning/deployment/health_checker.py

Reference (拿来主义):
  查看 agentos/governance/cross_module_health.py 上现有的模块健康检查方式,
  理解其模式, 特别是 per-module check 如何汇聚为 global health score。
  从 agentos/governance/fitness_aggregator.py 这个好例子开始 — 它的
  report→aggregate→get_trend 展示了多模块指标汇聚的模式。
  遵循该模式实现 HealthChecker, 让 deployment_orchestrator(M565) 可以
  持续监控已部署模型的健康状态, 并能在多个信号同时恶化时触发自动回滚.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.health_checker.v1"

_DEFAULT_WINDOW: int = 100


class HealthSignal:
    """A single health signal track."""

    __slots__ = ("name", "weight", "_samples", "threshold_min", "threshold_max")

    def __init__(
        self, name: str, weight: float = 1.0,
        threshold_min: Optional[float] = None,
        threshold_max: Optional[float] = None,
        window: int = _DEFAULT_WINDOW,
    ) -> None:
        self.name = name
        self.weight = weight
        self._samples: Deque[float] = deque(maxlen=window)
        self.threshold_min = threshold_min
        self.threshold_max = threshold_max

    def record(self, value: float) -> None:
        self._samples.append(value)

    def mean(self) -> float:
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    def is_healthy(self) -> bool:
        if not self._samples:
            return True
        avg = self.mean()
        if self.threshold_min is not None and avg < self.threshold_min:
            return False
        if self.threshold_max is not None and avg > self.threshold_max:
            return False
        return True

    def score(self) -> float:
        """Score from 0 (unhealthy) to 1 (healthy)."""
        if not self._samples:
            return 1.0
        avg = self.mean()
        if self.threshold_max is not None:
            if avg > self.threshold_max:
                return max(0.0, 1.0 - (avg - self.threshold_max) / max(self.threshold_max, 0.01))
        if self.threshold_min is not None:
            if avg < self.threshold_min:
                return max(0.0, avg / max(self.threshold_min, 0.01))
        return 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "mean": round(self.mean(), 4),
            "samples": len(self._samples),
            "healthy": self.is_healthy(),
            "score": round(self.score(), 4),
        }


class HealthChecker:
    """Multi-signal health checker for deployed models.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, window: int = _DEFAULT_WINDOW) -> None:
        self.window = window
        self._signals: Dict[str, HealthSignal] = {}
        self._alert_callbacks: List[Callable[[str, Dict[str, Any]], None]] = []
        self._check_count: int = 0
        self._unhealthy_count: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_signal(
        self,
        name: str,
        weight: float = 1.0,
        threshold_min: Optional[float] = None,
        threshold_max: Optional[float] = None,
    ) -> None:
        """Register a health signal."""
        self._signals[name] = HealthSignal(
            name=name, weight=weight,
            threshold_min=threshold_min, threshold_max=threshold_max,
            window=self.window,
        )

    def record(self, signal: str, value: float) -> None:
        """Record a value for a health signal."""
        if signal not in self._signals:
            self._signals[signal] = HealthSignal(signal, window=self.window)
        self._signals[signal].record(value)

    def record_batch(self, values: Dict[str, float]) -> None:
        """Record multiple signal values at once."""
        for signal, value in values.items():
            self.record(signal, value)

    def check_health(self) -> Dict[str, Any]:
        """Run health check across all signals.

        Returns:
            Dict with overall_healthy, composite_score, per-signal details.
        """
        self._check_count += 1
        signal_results = {}
        total_weight = 0.0
        weighted_score = 0.0
        all_healthy = True

        for name, signal in self._signals.items():
            info = signal.to_dict()
            signal_results[name] = info
            total_weight += signal.weight
            weighted_score += signal.score() * signal.weight
            if not signal.is_healthy():
                all_healthy = False

        composite = weighted_score / total_weight if total_weight > 0 else 1.0

        result = {
            "overall_healthy": all_healthy,
            "composite_score": round(composite, 4),
            "signals": signal_results,
            "check_number": self._check_count,
            "timestamp": time.time(),
        }

        if not all_healthy:
            self._unhealthy_count += 1
            for cb in self._alert_callbacks:
                try:
                    cb("health_degraded", result)
                except Exception as exc:
                    logger.warning("Alert callback error: %s", exc)
            self._fire_evolution("health_degraded", {
                "composite_score": composite,
                "unhealthy_signals": [
                    n for n, s in self._signals.items() if not s.is_healthy()
                ],
            })

        return result

    def is_healthy(self) -> bool:
        """Quick health check."""
        return all(s.is_healthy() for s in self._signals.values())

    def composite_score(self) -> float:
        """Get current composite health score."""
        total_weight = sum(s.weight for s in self._signals.values())
        if total_weight == 0:
            return 1.0
        weighted = sum(s.score() * s.weight for s in self._signals.values())
        return weighted / total_weight

    def add_alert_callback(self, cb: Callable[[str, Dict[str, Any]], None]) -> None:
        """Register an alert callback."""
        self._alert_callbacks.append(cb)

    def signal_names(self) -> List[str]:
        return list(self._signals.keys())

    def get_signal(self, name: str) -> Dict[str, Any]:
        if name not in self._signals:
            raise KeyError(f"Signal '{name}' not found")
        return self._signals[name].to_dict()

    def get_stats(self) -> Dict[str, Any]:
        return {
            "signal_count": len(self._signals),
            "check_count": self._check_count,
            "unhealthy_count": self._unhealthy_count,
            "composite_score": round(self.composite_score(), 4),
        }

    def reset(self) -> None:
        self._signals.clear()
        self._check_count = 0
        self._unhealthy_count = 0

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
