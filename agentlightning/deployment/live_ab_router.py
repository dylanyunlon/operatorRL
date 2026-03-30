"""
Live A/B Router — Real-time traffic splitting for model A/B tests.

Routes inference requests between multiple model variants based on
configurable split ratios, with per-variant metric tracking and
statistical significance testing.

Location: agentlightning/deployment/live_ab_router.py

Reference (拿来主义):
  查看 agentos/governance/ab_test_controller.py 上现有 ABTestController
  的流量分割方式, 理解其模式, 特别是 variant注册如何与流量路由分离。
  从 agentos/governance/model_ab_test.py 这个好例子开始 — 它展示了
  AB测试的统计显著性判断逻辑。
  遵循该模式实现 LiveABRouter, 让部署管线可以同时运行多个模型变体,
  并能自动检测统计显著性差异后选择最优变体.
"""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.live_ab_router.v1"


class ABVariant:
    """A single A/B test variant."""

    __slots__ = (
        "name", "version", "weight",
        "total", "successes", "total_reward",
    )

    def __init__(self, name: str, version: str, weight: float = 1.0) -> None:
        self.name = name
        self.version = version
        self.weight = weight
        self.total: int = 0
        self.successes: int = 0
        self.total_reward: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.total if self.total > 0 else 0.0

    @property
    def avg_reward(self) -> float:
        return self.total_reward / self.total if self.total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "version": self.version,
            "weight": self.weight, "total": self.total,
            "successes": self.successes,
            "success_rate": round(self.success_rate, 4),
            "avg_reward": round(self.avg_reward, 4),
        }


class LiveABRouter:
    """Routes traffic between A/B test variants.

    Attributes:
        test_name: Name of the A/B test.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, test_name: str = "default") -> None:
        self.test_name = test_name
        self._variants: Dict[str, ABVariant] = {}
        self._status: str = "idle"  # idle/running/concluded
        self._winner: Optional[str] = None
        self._stats = {"total_routed": 0}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_variant(self, name: str, version: str, weight: float = 1.0) -> None:
        self._variants[name] = ABVariant(name, version, weight)

    def start(self) -> None:
        if len(self._variants) < 2:
            raise RuntimeError("Need at least 2 variants for A/B test")
        self._status = "running"

    def route(self) -> str:
        """Route a request to a variant.

        Returns:
            Variant name.
        """
        if self._status == "concluded" and self._winner:
            return self._winner
        if not self._variants:
            raise RuntimeError("No variants registered")

        total_weight = sum(v.weight for v in self._variants.values())
        r = random.random() * total_weight
        cumulative = 0.0
        for name, variant in self._variants.items():
            cumulative += variant.weight
            if r <= cumulative:
                variant.total += 1
                self._stats["total_routed"] += 1
                return name
        # Fallback
        name = list(self._variants.keys())[-1]
        self._variants[name].total += 1
        return name

    def record_outcome(
        self, variant_name: str, success: bool = True, reward: float = 0.0,
    ) -> None:
        if variant_name in self._variants:
            v = self._variants[variant_name]
            if success:
                v.successes += 1
            v.total_reward += reward

    def compute_significance(
        self, variant_a: str, variant_b: str,
    ) -> Dict[str, Any]:
        """Compute statistical significance between two variants.

        Uses z-test for proportions.

        Returns:
            Dict with z_score, p_value_approx, is_significant.
        """
        a = self._variants.get(variant_a)
        b = self._variants.get(variant_b)
        if a is None or b is None:
            return {"error": "variant not found"}
        if a.total < 30 or b.total < 30:
            return {"is_significant": False, "reason": "insufficient_samples"}

        p_a = a.success_rate
        p_b = b.success_rate
        p_pool = (a.successes + b.successes) / (a.total + b.total)
        se = math.sqrt(p_pool * (1 - p_pool) * (1/a.total + 1/b.total)) if p_pool > 0 and p_pool < 1 else 1.0
        z = (p_a - p_b) / se if se > 0 else 0.0

        # Approximate p-value using normal CDF approximation
        p_value = 2.0 * (1.0 - min(1.0, 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))))

        return {
            "z_score": round(z, 4),
            "p_value_approx": round(p_value, 6),
            "is_significant": p_value < 0.05,
            "better_variant": variant_a if p_a > p_b else variant_b,
        }

    def conclude(self, winner: Optional[str] = None) -> Dict[str, Any]:
        """Conclude the test, selecting a winner.

        Args:
            winner: Force a winner. If None, picks best by success_rate.

        Returns:
            Conclusion summary.
        """
        if winner is None:
            best = max(self._variants.values(), key=lambda v: v.success_rate)
            winner = best.name
        self._winner = winner
        self._status = "concluded"
        self._fire_evolution("ab_test_concluded", {
            "test": self.test_name, "winner": winner,
        })
        return {
            "winner": winner,
            "version": self._variants[winner].version,
            "variants": {n: v.to_dict() for n, v in self._variants.items()},
        }

    @property
    def status(self) -> str:
        return self._status

    def get_results(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "status": self._status,
            "winner": self._winner,
            "variants": {n: v.to_dict() for n, v in self._variants.items()},
            "total_routed": self._stats["total_routed"],
        }

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY, "type": event_type,
                    "timestamp": time.time(), "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
