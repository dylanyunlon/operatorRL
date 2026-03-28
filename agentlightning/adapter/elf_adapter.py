"""
ELF Adapter — Facebook ELF environment adapter.

Adapts ELF's batch/reply format into AgentLightning spans.
References ELF/rlpytorch/trainer/trainer.py Evaluator.actor()
for batch format and reply message structure.

Location: agentlightning/adapter/elf_adapter.py

Reference: ELF/rlpytorch/trainer/trainer.py.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.adapter.elf_adapter.v1"

_ELF_SUPPORTED_GAMES = ["go", "atari", "rts", "minirts", "hex"]


class ELFAdapter:
    """Adapter from ELF batch format to AgentLightning spans.

    Converts ELF's (s, a, r, terminal) batch dicts and
    reply messages (pi, a, V) into operatorRL format.
    """

    def __init__(self, framework_name: str = "elf") -> None:
        self.framework_name = framework_name
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def adapt(self, batch: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert ELF batch to AgentLightning spans.

        ELF uses keys: s (states), a (actions), r (rewards),
        terminal (done flags) — matching the Evaluator.actor() format.

        Args:
            batch: ELF batch dict.

        Returns:
            List of span dicts.
        """
        states = batch.get("s", [])
        actions = batch.get("a", [])
        rewards = batch.get("r", [])
        terminals = batch.get("terminal", [])

        n = min(len(states), len(actions), len(rewards), len(terminals))
        if n == 0:
            return []

        spans = []
        for i in range(n):
            spans.append({
                "state": states[i],
                "action": actions[i],
                "reward": rewards[i],
                "done": terminals[i],
                "framework": self.framework_name,
                "step_index": i,
            })

        self._fire_evolution({"adapted_spans": len(spans)})
        return spans

    def convert_game_context(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Convert ELF game context to unified format.

        Args:
            ctx: ELF game context dict.

        Returns:
            Unified context dict.
        """
        return {
            "game_id": ctx.get("game_id", 0),
            "num_players": ctx.get("num_players", 1),
            "options": ctx.get("options", {}),
            "framework": self.framework_name,
        }

    def convert_reply(self, reply: dict[str, Any]) -> dict[str, Any]:
        """Convert ELF reply message to unified format.

        ELF replies: {pi: policy, a: action, V: value, rv: reply_version}

        Args:
            reply: ELF reply dict.

        Returns:
            Unified reply dict.
        """
        return {
            "policy": reply.get("pi", []),
            "action": reply.get("a", 0),
            "value": reply.get("V", 0.0),
        }

    def supported_games(self) -> list[str]:
        """List ELF-supported game types.

        Returns:
            List of game name strings.
        """
        return list(_ELF_SUPPORTED_GAMES)

    def build_evaluator_config(
        self,
        num_games: int = 100,
        batchsize: int = 32,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build ELF evaluator configuration.

        Args:
            num_games: Number of concurrent games.
            batchsize: Batch size for inference.

        Returns:
            Config dict.
        """
        config = {"num_games": num_games, "batchsize": batchsize}
        config.update(kwargs)
        return config

    def extract_stats(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Extract training/evaluation statistics.

        Args:
            raw: Raw stats dict.

        Returns:
            Filtered stats dict.
        """
        known_keys = {"win_rate", "avg_reward", "episodes", "steps", "loss"}
        return {k: v for k, v in raw.items() if k in known_keys}

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
