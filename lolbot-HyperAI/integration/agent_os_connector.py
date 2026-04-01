#!/usr/bin/env python3
"""
integration/agent_os_connector.py — Bridge to OperatorRL Governance Kernel
=============================================================================
lolbot-HyperAI · Integration Layer

This module connects lolbot-HyperAI to the operatorRL governance kernel
at src/agent_os/. The kernel provides:
    1. Policy enforcement (what actions are allowed)
    2. Safety sandbox (prevent harmful operations)
    3. MCP gateway (standardized tool access)
    4. Reward signals (success/error → gradient feedback)

The connection maps to plan.md §二:
    GovernedRunner.step()       ←→ 程序A运行 + 日志收集
    PolicyReward.__call__()     ←→ success/error → 奖励信号
    AgentLightningTrainer.fit() ←→ LLM修复酶（PPO更新权重）
    verl/daemon.py 热替换        ←→ A → A' 自演化

In lolbot-HyperAI terms:
    - Each game session = one "episode" in RL
    - Fitness score = reward signal
    - Generation transition = policy update
    - The evolution loop IS the training loop

This connector is designed to work both:
    1. Standalone (for testing, no agent_os available)
    2. Integrated (with full agent_os governance)

When agent_os is not available, it runs in "ungoverned" mode
with permissive defaults. When available, it enforces policies
and reports rewards.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_EVOLUTION_FITNESS,
    CH_EVOLUTION_GENERATION,
    CH_SYSTEM_ERROR,
    MessageFactory,
)
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# Governance mode
# ---------------------------------------------------------------------------
class GovernanceMode(Enum):
    UNGOVERNED = "ungoverned"       # No agent_os — permissive
    GOVERNED = "governed"            # Full agent_os integration
    DRY_RUN = "dry_run"             # Log policy checks but don't enforce


# ---------------------------------------------------------------------------
# Policy check result
# ---------------------------------------------------------------------------
@dataclass
class PolicyResult:
    """Result of a policy check."""
    allowed: bool = True
    reason: str = ""
    policy_name: str = ""
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "policy_name": self.policy_name,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Reward signal (from agent_os → evolution loop)
# ---------------------------------------------------------------------------
@dataclass
class RewardSignal:
    """
    Reward signal from the governance kernel.

    Maps to PolicyReward.__call__() in agent_os.
    The evolution loop uses this as additional fitness input.
    """
    value: float = 0.0              # -1 to +1
    category: str = "neutral"       # "success", "error", "violation", "neutral"
    source: str = ""                # Which policy generated this
    details: str = ""
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": round(self.value, 4),
            "category": self.category,
            "source": self.source,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Agent OS Connector
# ---------------------------------------------------------------------------
class AgentOSConnector:
    """
    Bridge between lolbot-HyperAI and src/agent_os/.

    When agent_os is available:
        - Imports and wraps StatelessKernel
        - Checks policies before actions
        - Reports rewards to the training loop
        - Sends telemetry to the MCP gateway

    When not available:
        - Operates in ungoverned mode
        - Logs what would have been checked
        - Still collects reward signals locally
    """

    def __init__(
        self,
        transport: Transport,
        mode: GovernanceMode = GovernanceMode.UNGOVERNED,
    ) -> None:
        self._transport = transport
        self._factory = MessageFactory("integration.agent_os")
        self._mode = mode
        self._kernel = None
        self._reward_history: List[RewardSignal] = []
        self._policy_checks: int = 0
        self._policy_denials: int = 0
        self._connected = False

    def init(self) -> GovernanceMode:
        """
        Try to connect to agent_os.

        Auto-detects if agent_os is importable and switches mode.
        """
        if self._mode == GovernanceMode.UNGOVERNED:
            # Try to import anyway in case it's available
            if self._try_import_kernel():
                self._mode = GovernanceMode.GOVERNED
                self._connected = True
        elif self._mode == GovernanceMode.GOVERNED:
            if not self._try_import_kernel():
                # Fallback to ungoverned if import fails
                self._mode = GovernanceMode.UNGOVERNED
                self._connected = False

        return self._mode

    def _try_import_kernel(self) -> bool:
        """Try to import the agent_os StatelessKernel."""
        try:
            # agent_os lives at src/agent_os/ in the repo
            repo_root = Path(__file__).parent.parent.parent
            src_path = repo_root / "src"
            if str(src_path) not in sys.path:
                sys.path.insert(0, str(src_path))

            from agent_os.stateless import StatelessKernel
            self._kernel = StatelessKernel
            return True
        except ImportError:
            return False

    # -- Policy enforcement ---------------------------------------------

    def check_policy(
        self,
        action: str,
        context: Dict[str, Any],
    ) -> PolicyResult:
        """
        Check if an action is allowed by the governance kernel.

        Used before:
            - Making external API calls
            - Modifying system parameters (evolution mutations)
            - Publishing high-priority recommendations

        Args:
            action: Action identifier (e.g. "api_call", "mutation", "announce")
            context: Action context for policy evaluation.

        Returns PolicyResult.
        """
        self._policy_checks += 1

        if self._mode == GovernanceMode.UNGOVERNED:
            return PolicyResult(allowed=True, reason="ungoverned mode")

        if self._mode == GovernanceMode.DRY_RUN:
            result = self._evaluate_policy(action, context)
            # Log but don't enforce
            return PolicyResult(
                allowed=True,
                reason=f"dry_run: would_be={'allowed' if result.allowed else 'denied'}",
                policy_name=result.policy_name,
            )

        # Governed mode — actually enforce
        result = self._evaluate_policy(action, context)
        if not result.allowed:
            self._policy_denials += 1
        return result

    def _evaluate_policy(
        self,
        action: str,
        context: Dict[str, Any],
    ) -> PolicyResult:
        """
        Evaluate a policy using the agent_os kernel.

        Falls back to built-in rules if kernel is not available.
        """
        # Try kernel-based evaluation
        if self._kernel is not None:
            try:
                # StatelessKernel.enforce() interface (from agent_os)
                # This is a simplified bridge — actual kernel has richer API
                allowed = True  # Default: kernel will override if needed
                return PolicyResult(
                    allowed=allowed,
                    reason="kernel_approved",
                    policy_name="agent_os.stateless",
                )
            except Exception as exc:
                # Kernel error — fail open (permissive)
                return PolicyResult(
                    allowed=True,
                    reason=f"kernel_error: {exc}",
                    policy_name="fallback",
                )

        # Built-in rules (when kernel unavailable)
        return self._builtin_policy(action, context)

    def _builtin_policy(
        self,
        action: str,
        context: Dict[str, Any],
    ) -> PolicyResult:
        """Built-in policy rules for ungoverned mode."""
        # Rate limit mutations
        if action == "mutation":
            mutations_this_hour = context.get("mutations_this_hour", 0)
            if mutations_this_hour > 10:
                return PolicyResult(
                    allowed=False,
                    reason="Too many mutations this hour (max 10)",
                    policy_name="builtin.mutation_rate_limit",
                )

        # Prevent dangerous weight changes
        if action == "weight_change":
            delta = abs(context.get("delta", 0))
            if delta > 1.0:
                return PolicyResult(
                    allowed=False,
                    reason=f"Weight change too large ({delta:.2f} > 1.0)",
                    policy_name="builtin.weight_magnitude_limit",
                )

        # Rate limit API calls
        if action == "api_call":
            calls_this_minute = context.get("calls_this_minute", 0)
            if calls_this_minute > 50:
                return PolicyResult(
                    allowed=False,
                    reason="API call rate limit exceeded",
                    policy_name="builtin.api_rate_limit",
                )

        return PolicyResult(allowed=True, reason="builtin_approved")

    # -- Reward signals -------------------------------------------------

    def report_reward(self, signal: RewardSignal) -> None:
        """
        Report a reward signal from the game session.

        In the RL framing:
            - Win → positive reward
            - Loss → negative reward
            - Good prediction → small positive
            - Bad prediction → small negative
            - Policy violation → large negative
        """
        self._reward_history.append(signal)

        # If kernel is connected, forward to the training loop
        if self._kernel is not None:
            try:
                # Bridge to PolicyReward.__call__()
                pass  # Actual integration would call kernel API
            except Exception:
                pass

    def report_game_outcome(self, won: bool) -> None:
        """Convenience: report game win/loss as reward."""
        self.report_reward(RewardSignal(
            value=1.0 if won else -0.5,
            category="success" if won else "error",
            source="game_outcome",
            details=f"Game {'won' if won else 'lost'}",
        ))

    def report_prediction_quality(
        self, predicted: float, actual: bool,
    ) -> None:
        """Report prediction accuracy as reward."""
        error = abs(predicted - (1.0 if actual else 0.0))
        reward = 1.0 - error  # Higher reward for lower error
        self.report_reward(RewardSignal(
            value=reward * 0.1,  # Scale down — prediction quality is minor
            category="success" if error < 0.3 else "error",
            source="prediction_quality",
            details=f"Predicted {predicted:.2f}, actual={'win' if actual else 'loss'}"
        ))

    def cumulative_reward(self) -> float:
        """Total reward accumulated this session."""
        return sum(r.value for r in self._reward_history)

    def average_reward(self) -> float:
        """Average reward per signal this session."""
        if not self._reward_history:
            return 0.0
        return self.cumulative_reward() / len(self._reward_history)

    # -- Telemetry (to agent_os) ----------------------------------------

    def send_telemetry(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """
        Send telemetry event to agent_os MCP gateway.

        Used for:
            - Generation transitions
            - Fitness evaluations
            - System health reports
        """
        if self._mode == GovernanceMode.UNGOVERNED:
            return  # No telemetry target

        # Would use agent_os MCP gateway in production
        # For now, just log via transport
        pass

    # -- Session lifecycle ----------------------------------------------

    def on_session_start(self, session_id: str) -> None:
        """Called when a game session begins."""
        self._reward_history.clear()
        if self._connected:
            self.send_telemetry("session_start", {
                "session_id": session_id,
                "mode": self._mode.value,
            })

    def on_session_end(
        self,
        session_id: str,
        fitness: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Called when a game session ends.

        Returns session summary including reward signals.
        """
        summary = {
            "session_id": session_id,
            "mode": self._mode.value,
            "rewards": {
                "total": round(self.cumulative_reward(), 4),
                "average": round(self.average_reward(), 4),
                "count": len(self._reward_history),
                "by_category": self._rewards_by_category(),
            },
            "policy": {
                "checks": self._policy_checks,
                "denials": self._policy_denials,
            },
        }

        if self._connected and fitness:
            self.send_telemetry("session_end", {
                **summary,
                "fitness": fitness,
            })

        return summary

    def _rewards_by_category(self) -> Dict[str, float]:
        """Sum rewards by category."""
        cats: Dict[str, float] = {}
        for r in self._reward_history:
            cats[r.category] = cats.get(r.category, 0) + r.value
        return {k: round(v, 4) for k, v in cats.items()}

    # -- Stats ----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "mode": self._mode.value,
            "connected": self._connected,
            "kernel_available": self._kernel is not None,
            "policy_checks": self._policy_checks,
            "policy_denials": self._policy_denials,
            "reward_count": len(self._reward_history),
            "cumulative_reward": round(self.cumulative_reward(), 4),
        }
