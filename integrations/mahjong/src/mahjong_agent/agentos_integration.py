"""
Mahjong AgentOS Integration — GovernedEnvironment bridge for mahjong.

Wraps the MahjongAgent as a Gym-like environment under AgentOS governance.
Each step = one mjai action, with policy checks and reward shaping.

Location: integrations/mahjong/src/mahjong_agent/agentos_integration.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "mahjong_agent.agentos_integration.v1"


@dataclass
class MahjongEnvConfig:
    """Configuration for mahjong governed environment."""
    max_steps: int = 200
    maturity_level: int = 0
    violation_penalty: float = -10.0
    success_bonus: float = 5.0
    step_penalty: float = -0.01


class MahjongGovernedEnv:
    """Gym-like governed environment for mahjong agent.

    Interface:
        obs = env.reset()
        obs, reward, done, info = env.step(action)

    Each action is an mjai-format dict. The environment applies
    policy checks and returns shaped rewards.
    """

    def __init__(self, config: MahjongEnvConfig | None = None) -> None:
        self.config = config or MahjongEnvConfig()
        self._step_count: int = 0
        self._done: bool = False
        self._violations: list[dict[str, Any]] = []
        self._episode_reward: float = 0.0
        self._game_state: dict[str, Any] = {}

    @property
    def step_count(self) -> int:
        return self._step_count

    def reset(self) -> dict[str, Any]:
        """Reset environment for new episode."""
        self._step_count = 0
        self._done = False
        self._violations.clear()
        self._episode_reward = 0.0
        self._game_state = {
            "hand": [],
            "discards": [],
            "doras": [],
            "scores": [25000, 25000, 25000, 25000],
            "round": 0,
            "seat": 0,
        }
        return self._observe()

    def step(self, action: dict[str, Any]) -> tuple[dict[str, Any], float, bool, dict[str, Any]]:
        """Execute one step: apply action, check policies, compute reward.

        Args:
            action: mjai-format action dict.

        Returns:
            (observation, reward, done, info) tuple.
        """
        self._step_count += 1
        violations = self._check_policies(action)
        reward = self._compute_reward(action, violations)
        self._episode_reward += reward

        # Check termination
        done = False
        if self._step_count >= self.config.max_steps:
            done = True
        if action.get("type") == "end_game":
            done = True
        if violations and any(v.get("severity") == "critical" for v in violations):
            done = True

        self._done = done
        info = {
            "violations": len(violations),
            "violation_details": violations,
            "episode_reward": self._episode_reward,
            "step": self._step_count,
        }

        return self._observe(), reward, done, info

    def _observe(self) -> dict[str, Any]:
        """Build observation from current game state."""
        return {
            "game_state": dict(self._game_state),
            "step_count": self._step_count,
            "maturity_level": self.config.maturity_level,
        }

    def _check_policies(self, action: dict[str, Any]) -> list[dict[str, Any]]:
        """Check action against governance policies."""
        violations = []
        action_type = action.get("type", "")

        # Test violation trigger
        if action_type == "__violation_test__":
            violations.append({
                "policy": "test_policy",
                "severity": "medium",
                "message": "Test violation triggered",
            })

        # Rate limiting check (simplified)
        if self._step_count > self.config.max_steps * 0.9:
            violations.append({
                "policy": "rate_limit",
                "severity": "low",
                "message": "Approaching step limit",
            })

        self._violations.extend(violations)
        return violations

    def _compute_reward(self, action: dict[str, Any], violations: list[dict[str, Any]]) -> float:
        """Compute shaped reward."""
        reward = self.config.step_penalty  # Base step penalty

        # Penalty for violations
        for v in violations:
            severity = v.get("severity", "low")
            if severity == "critical":
                reward += self.config.violation_penalty * 10
            elif severity == "high":
                reward += self.config.violation_penalty * 5
            elif severity == "medium":
                reward += self.config.violation_penalty
            else:
                reward += self.config.violation_penalty * 0.1

        # Bonus for valid game actions
        action_type = action.get("type", "")
        if action_type in ("dahai", "tsumo", "reach", "chi", "pon", "kan"):
            reward += 0.1  # Small positive for valid play

        return reward
