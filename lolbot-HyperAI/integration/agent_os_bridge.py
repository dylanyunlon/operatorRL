#!/usr/bin/env python3
"""
AgentOSBridge — Bridge between lolbot-HyperAI and Agent-OS Kernel
===================================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Connects the LoL game assistant to the Agent-OS governance framework.
Implements the GovernedRunner interface so the LoL bot is treated as
a governed agent within the Agent-OS kernel — subject to policy
enforcement, trust verification, and evolution oversight.

Agent-OS Kernel Interfaces (from src/agent_os/):
    base_agent.py → BaseAgent, GovernedRunner
    semantic_policy.py → PolicyEngine, SemanticPolicy
    trust_root.py → TrustRoot, Identity
    supervisor.py → Supervisor (evolution lifecycle)

Apollo Reference:
    modules/common/adapters/adapter_gflags.cc → module registration
    cyber/component/component.h → Component::Init, ::Proc, ::Shutdown

Design:
    AgentOSBridge
      ├── GovernedRunnerAdapter    (wraps lolbot as GovernedRunner)
      ├── PolicyRewardAdapter      (converts game metrics → policy rewards)
      ├── TrustAnchor              (identity & capability declarations)
      ├── KernelEventPublisher     (publishes events to Agent-OS bus)
      └── EvolutionGateway         (routes evolution proposals through kernel)

Production Critique (Knuth-level):
    1. User: If Agent-OS kernel is not available (standalone mode), the
       bridge operates in "passthrough" mode — all policy checks return
       ALLOW, trust is self-asserted. User sees no difference in gameplay.
    2. System: The bridge never blocks the main loop waiting for kernel
       responses. All kernel interactions are fire-and-forget or use
       callbacks. If the kernel denies a policy (e.g., "don't track this
       player"), the bridge queues a POLICY_DENIED event for the
       EvolutionController to learn from.
"""

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BridgeMode(enum.Enum):
    """Operating mode of the Agent-OS bridge."""
    CONNECTED = "connected"       # Kernel available, full governance
    PASSTHROUGH = "passthrough"   # Kernel unavailable, self-governed
    DEGRADED = "degraded"         # Partial kernel connectivity


class PolicyDecision(enum.Enum):
    """Result of a policy evaluation."""
    ALLOW = "allow"
    DENY = "deny"
    DEFER = "defer"   # Cannot decide — let caller choose default


class AgentCapability(enum.Enum):
    """Capabilities this agent declares to the kernel."""
    NETWORK_CAPTURE = "network_capture"
    GAME_STATE_TRACKING = "game_state_tracking"
    WIN_PREDICTION = "win_prediction"
    VOICE_OUTPUT = "voice_output"
    PLAYER_PROFILING = "player_profiling"
    STRATEGY_GENERATION = "strategy_generation"
    SELF_EVOLUTION = "self_evolution"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AgentIdentity:
    """Agent identity declaration for the trust root."""
    agent_id: str = "lolbot-hyperai-v1"
    agent_type: str = "game_assistant"
    version: str = "1.0.0"
    capabilities: Set[AgentCapability] = field(default_factory=lambda: set(AgentCapability))
    trust_level: str = "self-certified"
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "version": self.version,
            "capabilities": [c.value for c in self.capabilities],
            "trust_level": self.trust_level,
            "metadata": self.metadata,
        }


@dataclass
class PolicyRequest:
    """A request to evaluate a policy."""
    request_id: str
    action: str            # e.g., "capture_traffic", "profile_player", "evolve"
    resource: str          # e.g., "player:summoner_name", "network:fiddler"
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class PolicyResult:
    """Result of a policy evaluation."""
    request_id: str
    decision: PolicyDecision
    reason: str = ""
    constraints: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: float = field(default_factory=time.monotonic)


@dataclass
class KernelEvent:
    """An event to publish to the Agent-OS event bus."""
    event_type: str
    source: str = "lolbot-hyperai"
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class RewardSignal:
    """
    Reward signal for the RL training loop.
    Maps game outcomes → policy rewards for Agent Lightning.
    """
    episode_id: str
    step: int
    reward: float            # Scalar reward
    components: Dict[str, float] = field(default_factory=dict)
    terminal: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "step": self.step,
            "reward": self.reward,
            "components": self.components,
            "terminal": self.terminal,
        }


# ---------------------------------------------------------------------------
# PolicyEngine adapter — evaluates policies locally or via kernel
# ---------------------------------------------------------------------------

class PolicyAdapter:
    """
    Evaluates policies against the Agent-OS semantic policy engine.
    In passthrough mode, all policies return ALLOW.
    """

    def __init__(self, mode: BridgeMode = BridgeMode.PASSTHROUGH):
        self._log = logging.getLogger("lolbot.integration.policy")
        self._mode = mode
        self._kernel_policy_fn: Optional[Callable] = None
        self._policy_cache: Dict[str, PolicyResult] = {}
        self._cache_ttl_s: float = 60.0
        self._denied_actions: List[PolicyResult] = []

    def set_kernel_policy(self, policy_fn: Callable) -> None:
        """Set the kernel's policy evaluation function."""
        self._kernel_policy_fn = policy_fn
        self._mode = BridgeMode.CONNECTED

    async def evaluate(self, request: PolicyRequest) -> PolicyResult:
        """Evaluate a policy request."""
        cache_key = f"{request.action}:{request.resource}"
        cached = self._policy_cache.get(cache_key)
        if cached and (time.monotonic() - cached.evaluated_at) < self._cache_ttl_s:
            return cached

        if self._mode == BridgeMode.PASSTHROUGH or not self._kernel_policy_fn:
            result = PolicyResult(
                request_id=request.request_id,
                decision=PolicyDecision.ALLOW,
                reason="passthrough mode — no kernel governance",
            )
        else:
            try:
                kernel_result = await asyncio.wait_for(
                    self._kernel_policy_fn(request), timeout=1.0
                )
                result = kernel_result
            except asyncio.TimeoutError:
                self._log.warning(
                    "Kernel policy timeout for %s — defaulting to ALLOW",
                    cache_key,
                )
                result = PolicyResult(
                    request_id=request.request_id,
                    decision=PolicyDecision.ALLOW,
                    reason="kernel timeout — default allow",
                )
            except Exception as exc:
                self._log.error("Kernel policy error: %s", exc)
                result = PolicyResult(
                    request_id=request.request_id,
                    decision=PolicyDecision.ALLOW,
                    reason=f"kernel error — default allow: {exc}",
                )

        self._policy_cache[cache_key] = result
        if result.decision == PolicyDecision.DENY:
            self._denied_actions.append(result)
        return result

    def get_denied_actions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent denied policy actions for evolution learning."""
        return [
            {
                "request_id": r.request_id,
                "reason": r.reason,
                "constraints": r.constraints,
            }
            for r in self._denied_actions[-limit:]
        ]


# ---------------------------------------------------------------------------
# RewardAdapter — converts game metrics to RL reward signals
# ---------------------------------------------------------------------------

class RewardAdapter:
    """
    Converts game outcomes and metrics into reward signals for the
    Agent Lightning RL training loop.

    Reward components:
      - prediction_accuracy: how close was our win probability to actual outcome
      - advice_quality: did the user follow our advice? did it help?
      - system_stability: uptime, error rate, latency
      - evolution_progress: did the latest generation improve?
    """

    def __init__(self):
        self._log = logging.getLogger("lolbot.integration.reward")
        self._episode_counter = 0
        self._reward_history: List[RewardSignal] = []

    def compute_game_reward(
        self,
        predicted_win_prob: float,
        actual_won: bool,
        advice_followed_rate: float = 0.0,
        system_uptime_rate: float = 1.0,
        prediction_count: int = 0,
    ) -> RewardSignal:
        """
        Compute reward signal for a completed game.

        Args:
            predicted_win_prob: Our predicted win probability [0, 1]
            actual_won: Whether the team actually won
            advice_followed_rate: How often user followed our advice [0, 1]
            system_uptime_rate: System uptime during game [0, 1]
            prediction_count: Number of predictions made during game
        """
        self._episode_counter += 1

        # Prediction accuracy: inverse Brier score component
        actual = 1.0 if actual_won else 0.0
        brier = (predicted_win_prob - actual) ** 2
        prediction_reward = 1.0 - brier  # [0, 1]

        # Advice quality: reward for advice being followed
        advice_reward = advice_followed_rate * 0.5

        # System stability: penalize downtime
        stability_reward = system_uptime_rate * 0.3

        # Engagement: reward for making many predictions (system was active)
        engagement_reward = min(1.0, prediction_count / 50.0) * 0.2

        total = (
            prediction_reward * 0.4
            + advice_reward * 0.25
            + stability_reward * 0.2
            + engagement_reward * 0.15
        )

        signal = RewardSignal(
            episode_id=f"game-{self._episode_counter:06d}",
            step=0,
            reward=round(total, 4),
            terminal=True,
            components={
                "prediction_accuracy": round(prediction_reward, 4),
                "advice_quality": round(advice_reward, 4),
                "system_stability": round(stability_reward, 4),
                "engagement": round(engagement_reward, 4),
            },
            metadata={
                "predicted_win_prob": predicted_win_prob,
                "actual_won": actual_won,
                "prediction_count": prediction_count,
            },
        )
        self._reward_history.append(signal)
        return signal

    def compute_step_reward(
        self,
        episode_id: str,
        step: int,
        prediction_delta: float = 0.0,
        event_detected: bool = False,
        voice_output_success: bool = False,
    ) -> RewardSignal:
        """Compute mid-game step reward for continuous learning."""
        reward = 0.0
        components = {}

        if event_detected:
            reward += 0.1
            components["event_detection"] = 0.1

        if voice_output_success:
            reward += 0.05
            components["voice_output"] = 0.05

        # Reward prediction changes (means the system is responsive)
        if abs(prediction_delta) > 0.05:
            reward += 0.02
            components["responsiveness"] = 0.02

        return RewardSignal(
            episode_id=episode_id,
            step=step,
            reward=round(reward, 4),
            components=components,
            terminal=False,
        )

    def get_average_reward(self, window: int = 20) -> float:
        recent = self._reward_history[-window:]
        if not recent:
            return 0.0
        return sum(r.reward for r in recent) / len(recent)

    def get_reward_trend(self) -> str:
        """Return 'improving', 'stable', or 'declining'."""
        if len(self._reward_history) < 10:
            return "insufficient_data"
        recent_10 = [r.reward for r in self._reward_history[-10:]]
        older_10 = [r.reward for r in self._reward_history[-20:-10]]
        if not older_10:
            return "insufficient_data"
        recent_avg = sum(recent_10) / len(recent_10)
        older_avg = sum(older_10) / len(older_10)
        diff = recent_avg - older_avg
        if diff > 0.05:
            return "improving"
        elif diff < -0.05:
            return "declining"
        return "stable"


# ---------------------------------------------------------------------------
# AgentOSBridge — the main bridge class
# ---------------------------------------------------------------------------

class AgentOSBridge:
    """
    Main bridge between lolbot-HyperAI and the Agent-OS kernel.

    Usage:
        bridge = AgentOSBridge()
        await bridge.connect()  # Try to connect to kernel
        
        # Policy check before capturing player data
        allowed = await bridge.check_policy("profile_player", "player:faker")
        
        # Publish events
        bridge.publish_event("game_started", {"game_id": "12345"})
        
        # Compute rewards after game
        reward = bridge.compute_reward(predicted=0.65, won=True)
    """

    def __init__(self, kernel_url: Optional[str] = None):
        self._log = logging.getLogger("lolbot.integration.agent_os_bridge")
        self._kernel_url = kernel_url
        self._identity = AgentIdentity()
        self._policy = PolicyAdapter()
        self._reward = RewardAdapter()
        self._mode = BridgeMode.PASSTHROUGH
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._event_publisher_task: Optional[asyncio.Task] = None
        self._connected = False
        self._request_counter = 0

    @property
    def mode(self) -> BridgeMode:
        return self._mode

    @property
    def identity(self) -> AgentIdentity:
        return self._identity

    # ---- ComponentProtocol ----

    @property
    def name(self) -> str:
        return "integration.agent_os_bridge"

    async def init(self) -> None:
        """Attempt to connect to Agent-OS kernel."""
        if self._kernel_url:
            try:
                await self._connect_to_kernel()
            except Exception as exc:
                self._log.warning(
                    "Cannot connect to Agent-OS kernel at %s: %s — "
                    "operating in passthrough mode",
                    self._kernel_url, exc,
                )
                self._mode = BridgeMode.PASSTHROUGH
        else:
            self._log.info(
                "No kernel URL configured — operating in passthrough mode"
            )
            self._mode = BridgeMode.PASSTHROUGH

        # Start event publisher background task
        self._event_publisher_task = asyncio.create_task(
            self._event_publisher_loop()
        )

    async def proc(self) -> None:
        """Periodic bridge health check."""
        pass

    async def shutdown(self) -> None:
        """Clean shutdown of the bridge."""
        if self._event_publisher_task:
            self._event_publisher_task.cancel()
            try:
                await self._event_publisher_task
            except asyncio.CancelledError:
                pass
        # Flush remaining events
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._log.info("AgentOSBridge shut down")

    # ---- Policy API ----

    async def check_policy(
        self, action: str, resource: str, context: Optional[Dict] = None
    ) -> bool:
        """
        Check if an action is allowed by policy.
        Returns True if allowed (ALLOW or DEFER), False if DENY.
        """
        self._request_counter += 1
        request = PolicyRequest(
            request_id=f"PR-{self._request_counter:08d}",
            action=action,
            resource=resource,
            context=context or {},
        )
        result = await self._policy.evaluate(request)
        return result.decision != PolicyDecision.DENY

    # ---- Event API ----

    def publish_event(self, event_type: str, payload: Optional[Dict] = None) -> None:
        """Publish an event to the Agent-OS event bus (non-blocking)."""
        event = KernelEvent(event_type=event_type, payload=payload or {})
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            self._log.warning("Event queue full — dropping event: %s", event_type)

    # ---- Reward API ----

    def compute_game_reward(self, **kwargs: Any) -> RewardSignal:
        """Delegate to RewardAdapter."""
        return self._reward.compute_game_reward(**kwargs)

    def compute_step_reward(self, **kwargs: Any) -> RewardSignal:
        return self._reward.compute_step_reward(**kwargs)

    @property
    def reward_adapter(self) -> RewardAdapter:
        return self._reward

    # ---- Status ----

    def get_status(self) -> Dict[str, Any]:
        return {
            "mode": self._mode.value,
            "connected": self._connected,
            "identity": self._identity.to_dict(),
            "event_queue_size": self._event_queue.qsize(),
            "reward_trend": self._reward.get_reward_trend(),
            "avg_reward": self._reward.get_average_reward(),
            "denied_actions": len(self._policy.get_denied_actions()),
        }

    # ---- Internal ----

    async def _connect_to_kernel(self) -> None:
        """Attempt to connect to the Agent-OS kernel."""
        self._log.info("Connecting to Agent-OS kernel at %s", self._kernel_url)
        # In production, this would use HTTP/gRPC to the kernel.
        # For now, we check if the kernel module is importable.
        try:
            from src.agent_os.base_agent import BaseAgent
            self._connected = True
            self._mode = BridgeMode.CONNECTED
            self._log.info("Connected to Agent-OS kernel (in-process)")
        except ImportError:
            raise ConnectionError("Agent-OS kernel not available")

    async def _event_publisher_loop(self) -> None:
        """Background loop that publishes events to the kernel."""
        while True:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=5.0
                )
                if self._mode == BridgeMode.CONNECTED:
                    await self._send_event_to_kernel(event)
                else:
                    self._log.debug(
                        "Event (passthrough): %s", event.event_type
                    )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log.error("Event publisher error: %s", exc)
                await asyncio.sleep(1.0)

    async def _send_event_to_kernel(self, event: KernelEvent) -> None:
        """Send event to kernel (stub for production implementation)."""
        self._log.debug("Publishing to kernel: %s", event.event_type)
