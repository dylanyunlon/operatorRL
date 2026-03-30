"""
Graceful Degradation Engine — Automatic fallback under stress.

Monitors system load and quality metrics, automatically downgrading
to simpler/faster inference modes when the system is under stress.
Supports multi-level degradation: full model → light model → rules.

Location: agentlightning/deployment/graceful_degradation.py

Reference (拿来主义):
  查看 agentlightning/inference/confidence_calibrator.py(M552) 上现有
  置信度阈值判断方式, 理解其模式, 特别是低置信度时如何触发兜底。
  从 integrations/lol/src/lol_agent/decision_engine.py 这个好例子开始 —
  它的 advantage→action 映射展示了多级决策降级模式。
  遵循该模式实现 GracefulDegradation, 让推理管线在过载或模型异常时
  自动降级到规则引擎, 保证最坏情况下仍有合理输出.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.graceful_degradation.v1"


class DegradationLevel:
    """Single degradation level."""

    __slots__ = ("name", "priority", "handler", "description")

    def __init__(
        self, name: str, priority: int,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        description: str = "",
    ) -> None:
        self.name = name
        self.priority = priority  # lower = better (0 = full model)
        self.handler = handler
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "priority": self.priority,
            "description": self.description,
        }


class GracefulDegradation:
    """Manages automatic inference degradation under stress.

    Attributes:
        current_level: Current degradation level name.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._levels: Dict[str, DegradationLevel] = {}
        self._level_order: List[str] = []  # sorted by priority
        self._current_level_name: str = ""
        self._triggers: Dict[str, Callable[[], bool]] = {}
        self._degradation_count: int = 0
        self._recovery_count: int = 0
        self._stats = {"total_requests": 0, "per_level": {}}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_level(
        self, name: str, priority: int,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        description: str = "",
    ) -> None:
        level = DegradationLevel(name, priority, handler, description)
        self._levels[name] = level
        self._level_order = sorted(self._levels.keys(), key=lambda n: self._levels[n].priority)
        if not self._current_level_name:
            self._current_level_name = self._level_order[0]

    def register_trigger(
        self, name: str, check_fn: Callable[[], bool],
    ) -> None:
        """Register a degradation trigger.

        Args:
            name: Trigger name.
            check_fn: Returns True when degradation should occur.
        """
        self._triggers[name] = check_fn

    @property
    def current_level(self) -> str:
        return self._current_level_name

    def evaluate_triggers(self) -> Dict[str, Any]:
        """Check all triggers and adjust level.

        Returns:
            Dict with action taken and current level.
        """
        any_triggered = False
        triggered_names: List[str] = []
        for name, check_fn in self._triggers.items():
            try:
                if check_fn():
                    any_triggered = True
                    triggered_names.append(name)
            except Exception as exc:
                logger.warning("Trigger check error %s: %s", name, exc)

        if any_triggered:
            return self.degrade(reason=",".join(triggered_names))
        else:
            return self.try_recover()

    def degrade(self, reason: str = "manual") -> Dict[str, Any]:
        """Move to next degradation level."""
        current_idx = self._level_order.index(self._current_level_name) if self._current_level_name in self._level_order else 0
        if current_idx < len(self._level_order) - 1:
            old = self._current_level_name
            self._current_level_name = self._level_order[current_idx + 1]
            self._degradation_count += 1
            self._fire_evolution("degraded", {
                "from": old, "to": self._current_level_name, "reason": reason,
            })
            return {"action": "degraded", "from": old, "to": self._current_level_name}
        return {"action": "already_at_lowest", "level": self._current_level_name}

    def try_recover(self) -> Dict[str, Any]:
        """Try to recover to a better level."""
        current_idx = self._level_order.index(self._current_level_name) if self._current_level_name in self._level_order else 0
        if current_idx > 0:
            old = self._current_level_name
            self._current_level_name = self._level_order[current_idx - 1]
            self._recovery_count += 1
            return {"action": "recovered", "from": old, "to": self._current_level_name}
        return {"action": "already_at_best", "level": self._current_level_name}

    def set_level(self, name: str) -> None:
        if name not in self._levels:
            raise KeyError(f"Level '{name}' not registered")
        self._current_level_name = name

    def serve(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Serve a request at current degradation level.

        Args:
            request: Input data dict.

        Returns:
            Output from the current level's handler.
        """
        self._stats["total_requests"] += 1
        level = self._levels.get(self._current_level_name)
        if level is None:
            raise RuntimeError("No degradation level active")
        self._stats.setdefault("per_level", {})
        self._stats["per_level"][level.name] = self._stats["per_level"].get(level.name, 0) + 1
        result = level.handler(request)
        result["_degradation_level"] = level.name
        return result

    def list_levels(self) -> List[Dict[str, Any]]:
        return [self._levels[n].to_dict() for n in self._level_order]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "current_level": self._current_level_name,
            "degradation_count": self._degradation_count,
            "recovery_count": self._recovery_count,
            **self._stats,
        }

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY, "type": event_type,
                    "timestamp": time.time(), "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
