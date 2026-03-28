"""
Multi-Game Trainer — Unified training loop across game types.

Provides game registration, per-game training steps, checkpoint
save/load, and metrics aggregation. Adapted from DI-star's
RLLearner and agentlightning's Trainer base class.

Location: agentlightning/trainer/multi_game_trainer.py

Reference: DI-star/distar/agent/default/rl_learner.py,
           agentlightning/trainer/trainer.py.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.trainer.multi_game_trainer.v1"


class MultiGameTrainer:
    """Unified trainer supporting multiple game types simultaneously.

    Each game registers its own config. Training steps route data
    to the correct game-specific processing pipeline.
    """

    def __init__(self) -> None:
        self.registered_games: dict[str, dict[str, Any]] = {}
        self.global_step: int = 0
        self._metrics_buffer: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_game(self, game: str, config: dict[str, Any]) -> None:
        """Register a game for training.

        Args:
            game: Game identifier.
            config: Game-specific training config.
        """
        self.registered_games[game] = {
            "config": config,
            "step_count": 0,
            "total_reward": 0.0,
        }
        logger.info("Registered game '%s' for training", game)

    def train_step(self, batch: dict[str, Any]) -> dict[str, Any]:
        """Execute one training step.

        Args:
            batch: Training batch with 'game', 'states', 'actions', 'rewards'.

        Returns:
            Step metrics dict.

        Raises:
            KeyError: If game not registered.
        """
        game = batch["game"]
        if game not in self.registered_games:
            raise KeyError(f"Game '{game}' not registered. Register with register_game() first.")

        game_info = self.registered_games[game]
        rewards = batch.get("rewards", [])
        total_r = sum(rewards) if rewards else 0.0

        game_info["step_count"] += 1
        game_info["total_reward"] += total_r
        self.global_step += 1

        metrics = {
            "global_step": self.global_step,
            "game": game,
            "game_step": game_info["step_count"],
            "batch_reward": total_r,
            "avg_reward": game_info["total_reward"] / game_info["step_count"],
        }
        self._metrics_buffer.append(metrics)

        self._fire_evolution(metrics)
        return metrics

    def get_metrics(self) -> dict[str, Any]:
        """Get aggregated training metrics.

        Returns:
            Metrics dict.
        """
        return {
            "global_step": self.global_step,
            "games": {
                game: {
                    "step_count": info["step_count"],
                    "avg_reward": info["total_reward"] / max(info["step_count"], 1),
                }
                for game, info in self.registered_games.items()
            },
        }

    def save_checkpoint(self) -> dict[str, Any]:
        """Save training state to checkpoint dict.

        Returns:
            Checkpoint dict.
        """
        return {
            "global_step": self.global_step,
            "registered_games": {
                game: dict(info)
                for game, info in self.registered_games.items()
            },
        }

    def load_checkpoint(self, ckpt: dict[str, Any]) -> None:
        """Restore training state from checkpoint.

        Args:
            ckpt: Checkpoint dict.
        """
        self.global_step = ckpt.get("global_step", 0)
        for game, info in ckpt.get("registered_games", {}).items():
            self.registered_games[game] = dict(info)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
