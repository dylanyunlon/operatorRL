"""
DI-star Adapter — DI-engine training loop adapter.

Adapts DI-star's replay/training data format into AgentLightning
spans. References DI-star's replay_decoder.py for obs/action
format conversion and rl_learner.py for training loop structure.

Location: agentlightning/adapter/distar_adapter.py

Reference: DI-star/distar/agent/default/replay_decoder.py,
           DI-star/distar/agent/default/rl_learner.py.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.adapter.distar_adapter.v1"


class DIStarAdapter:
    """Adapter from DI-star replay/training format to AgentLightning spans.

    Converts DI-star's observation (entity_info, spatial_info) and
    action (action_type, target_unit, target_location) formats into
    operatorRL's unified state/action/reward span format.
    """

    def __init__(self, game_name: str = "starcraft2") -> None:
        self.game_name = game_name
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def adapt(self, replay: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert DI-star replay data to AgentLightning training spans.

        Args:
            replay: Dict with "steps" list and "result" string.

        Returns:
            List of span dicts with state/action/reward.
        """
        steps = replay.get("steps", [])
        result = replay.get("result", "unknown")
        if not steps:
            return []

        reward_sign = 1.0 if result == "win" else (-1.0 if result == "loss" else 0.0)
        spans = []
        for i, step in enumerate(steps):
            spans.append({
                "state": step.get("obs", []),
                "action": step.get("action", 0),
                "reward": step.get("reward", 0.0) + reward_sign * (i / max(len(steps), 1)),
                "game": self.game_name,
                "step_index": i,
            })

        self._fire_evolution({"adapted_spans": len(spans), "result": result})
        return spans

    def convert_obs(self, distar_obs: dict[str, Any]) -> dict[str, Any]:
        """Convert DI-star observation format to unified format.

        DI-star uses entity_info/spatial_info/scalar_info. We flatten
        into entities/spatial/scalars.

        Args:
            distar_obs: DI-star observation dict.

        Returns:
            Unified observation dict.
        """
        return {
            "entities": distar_obs.get("entity_info", {}).get("units", []),
            "spatial": distar_obs.get("spatial_info", {}).get("map", []),
            "scalars": distar_obs.get("scalar_info", {}),
        }

    def convert_action(self, distar_action: dict[str, Any]) -> dict[str, Any]:
        """Convert DI-star action format to unified format.

        Args:
            distar_action: DI-star action dict.

        Returns:
            Unified action dict.
        """
        return {
            "type": distar_action.get("action_type", 0),
            "target_unit": distar_action.get("target_unit", None),
            "target_location": distar_action.get("target_location", None),
        }

    def compute_reward(
        self, result: str, stats: Optional[dict[str, Any]] = None
    ) -> float:
        """Compute reward from game result and stats.

        Args:
            result: "win", "loss", or "draw".
            stats: Optional game statistics.

        Returns:
            Reward float.
        """
        base = {"win": 1.0, "loss": -1.0, "draw": 0.0}.get(result, 0.0)
        score_bonus = 0.0
        if stats:
            score_bonus = min(stats.get("score", 0) / 1000.0, 0.5)
        return base + score_bonus

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
