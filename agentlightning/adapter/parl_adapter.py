"""
PARL Adapter — PaddlePaddle RL framework adapter.

Adapts PARL's experience/weight format into AgentLightning spans.
References PARL/parl/core/agent_base.py for agent interface and
PARL/parl/algorithms/torch/ppo.py for training data format.

Location: agentlightning/adapter/parl_adapter.py

Reference: PARL/parl/core/agent_base.py, PARL/parl/algorithms/torch/ppo.py.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.adapter.parl_adapter.v1"


class PARLAdapter:
    """Adapter from PARL experience format to AgentLightning spans.

    Converts PARL's (obs, actions, rewards, dones) tuples and
    weight dicts into operatorRL's unified format.
    """

    def __init__(self, framework_name: str = "parl") -> None:
        self.framework_name = framework_name
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def adapt(self, experience: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert PARL experience batch to AgentLightning spans.

        Args:
            experience: Dict with obs, actions, rewards, dones lists.

        Returns:
            List of span dicts.
        """
        obs = experience.get("obs", [])
        actions = experience.get("actions", [])
        rewards = experience.get("rewards", [])
        dones = experience.get("dones", [])

        n = min(len(obs), len(actions), len(rewards), len(dones))
        if n == 0:
            return []

        spans = []
        for i in range(n):
            spans.append({
                "state": obs[i],
                "action": actions[i],
                "reward": rewards[i],
                "done": dones[i],
                "framework": self.framework_name,
                "step_index": i,
            })

        self._fire_evolution({"adapted_spans": len(spans), "framework": self.framework_name})
        return spans

    def convert_weights(self, parl_weights: dict[str, Any]) -> dict[str, Any]:
        """Convert PARL weight dict to unified format.

        PARL uses {layer_name: numpy_array} — we preserve the structure
        but tag with framework metadata.

        Args:
            parl_weights: PARL model weights dict.

        Returns:
            Unified weights dict.
        """
        if not parl_weights:
            return {}
        return {
            k: {"data": v, "source": self.framework_name}
            for k, v in parl_weights.items()
        }

    def build_training_config(
        self,
        lr: float = 0.001,
        gamma: float = 0.99,
        batch_size: int = 64,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build training configuration dict.

        Args:
            lr: Learning rate.
            gamma: Discount factor.
            batch_size: Training batch size.

        Returns:
            Config dict.
        """
        config = {"lr": lr, "gamma": gamma, "batch_size": batch_size}
        config.update(kwargs)
        return config

    def extract_metrics(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Extract relevant training metrics from raw output.

        Args:
            raw: Raw metrics dict from PARL training.

        Returns:
            Filtered metrics dict.
        """
        known_keys = {"loss", "entropy", "value_loss", "policy_loss", "kl_divergence"}
        return {k: v for k, v in raw.items() if k in known_keys}

    def supported_backends(self) -> list[str]:
        """List supported compute backends.

        Returns:
            List of backend name strings.
        """
        return ["torch", "paddle"]

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
