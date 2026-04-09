"""
ControlComponent — Unified output dispatch (voice + overlay + log).
====================================================================
lolbot-HyperAI · Control Layer

The Apollo ``control`` analog: reads strategy decisions from the planning
pipeline and dispatches them to voice, overlay, and structured log outputs.

Architecture position:
    modules/control/control_component.py   <- YOU ARE HERE
    +- Reads: /lol/strategy (StrategyAdvice from planning)
    +- Reads: /lol/macro_decision (MacroDecision from planning)
    +- Reads: /lol/win_prediction (WinPrediction from prediction)
    +- Reads: /lol/teamfight_assessment (from prediction)
    +- Delegates to: ActionDispatcher, OverlayRenderer, VoiceNarrator
    +- Publishes: /lol/control_status (StatusMessage)
    +- Publishes: /lol/voice_queue (VoiceCommand for downstream TTS)

Apollo reference:
    modules/control/control_component.cc -- Proc()
    modules/control/controller_agent.cc  -- dispatch to subcontrollers

Design notes (Claude11 refactor):
    - OutputChannel abstraction: voice/overlay/log registered via interface
    - Apollo-style 3-line Proc(): drain -> dispatch -> flush
    - ManagedComponent mixin for lifecycle + circuit breaker
    - Priority queue with cooldown-aware dedup
    - Per-channel health tracking and graceful degradation
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.component_base import (
    ComponentDependency,
    LifecycleState,
    ManagedComponent,
)
from modules.common.status.error_code import Status, StatusMessage
from modules.common.adapters.game_messages import (
    GameSnapshot,
    StrategyAdvice,
    VoiceCommand,
    WinPrediction,
)
from modules.control.action_dispatch.action_dispatcher import (
    ActionDispatcher,
    ActionCategory,
    ActionPriority,
    DispatchAction,
)
from modules.control.overlay.overlay_renderer import OverlayRenderer
from modules.control.voice_output.voice_narrator import (
    VoiceNarratorComponent as VoiceNarrator,
)
# Claude19: Wire Claude18 VoicePriorityQueue + new GameNarrator
from modules.control.voice_output.voice_priority_queue import (
    VoicePriorityQueue,
    VoicePriority,
    VoiceEntry,
)
from modules.control.narration.game_narrator import (
    GameNarrator,
    NarrationLine,
)

# Claude26: Apollo-style code/interface separation — delegate to sub-modules
from modules.control.channel.output_channel import (
    OutputChannel, OutputChannelState, OutputChannelStats,
)
from modules.control.channel.voice_channel import VoiceOutputChannel
from modules.control.channel.overlay_channel import OverlayOutputChannel
from modules.control.channel.log_channel import LogOutputChannel
from modules.control.dispatch.safety_guard import SafetyGuard
from modules.control.dispatch.cooldown_tracker import CooldownTracker
from modules.control.dispatch.dedup_filter import DedupFilter
from modules.control.dispatch.rate_limiter import DispatchRateLimiter
from modules.control.dispatch.effectiveness_tracker import ActionEffectivenessTracker

logger = get_logger("control")

# --- Constants ---------------------------------------------------------------

_CONTROL_INTERVAL_MS = 200.0    # 5Hz
_WARN_THRESHOLD_MS = 150.0
_WIN_PROB_ANNOUNCE_INTERVAL_S = 30.0
_WIN_PROB_CHANGE_THRESHOLD = 0.10
_MAX_OUTPUT_QUEUE = 64
_DISPATCH_COOLDOWN_S = 2.0


# --- OutputChannel abstraction -----------------------------------------------

# Claude26: OutputChannelState moved to modules/control/channel/output_channel.py
# Claude26: OutputChannelStats moved to modules/control/channel/output_channel.py
# Claude26: OutputChannel moved to modules/control/channel/output_channel.py
# --- Built-in output channels ------------------------------------------------

# Claude26: VoiceOutputChannel moved to modules/control/channel/voice_channel.py
# Claude26: OverlayOutputChannel moved to modules/control/channel/overlay_channel.py
# Claude26: LogOutputChannel moved to modules/control/channel/log_channel.py
# --- ControlComponent --------------------------------------------------------

class ControlComponent(TimerComponent, ManagedComponent):
    """Control component: dispatches strategy to output channels.

    Apollo-style Proc() pattern (3-step):
    1. Drain strategy + prediction readers
    2. Route actions through registered OutputChannels
    3. Flush overlay and publish status

    Claude11 improvements over original:
    - OutputChannel abstraction (voice/overlay/log via registry)
    - ManagedComponent lifecycle + circuit breaker
    - Per-channel health tracking and auto-disable
    - Win probability announcement with hysteresis
    - Configurable priority thresholds per channel
    """

    COMPONENT_NAME = "control"
    DEPENDENCIES = [
        ComponentDependency("perception", required=False),
        ComponentDependency("planning", required=False),
    ]
    VERSION = "2.0.0"

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="control",
                interval_ms=_CONTROL_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        # Readers
        self._strategy_reader: Optional[Reader[StrategyAdvice]] = None
        self._win_pred_reader: Optional[Reader[WinPrediction]] = None
        self._game_state_reader: Optional[Reader[GameSnapshot]] = None

        # Writers
        self._status_writer: Optional[Writer[StatusMessage]] = None
        self._voice_queue_writer: Optional[Writer[VoiceCommand]] = None

        # Output channels (registered in Init)
        self._channels: Dict[str, OutputChannel] = {}

        # Legacy sub-modules (kept for backward compat)
        self._dispatcher: Optional[ActionDispatcher] = None
        self._overlay: Optional[OverlayRenderer] = None

        # State
        self._tick_count: int = 0
        self._dispatch_count: int = 0
        self._last_win_announce_time: float = 0.0
        self._last_announced_win_prob: float = 0.5
        self._pending_actions: Deque[DispatchAction] = deque(
            maxlen=_MAX_OUTPUT_QUEUE,
        )

        # Claude19: VoicePriorityQueue replaces simple dedup cooldown for voice
        self._voice_priority_queue: Optional[VoicePriorityQueue] = None
        self._game_narrator: Optional[GameNarrator] = None
        self._last_narrated_win_prob: float = 0.5

    # -- Init / Shutdown (Apollo lifecycle) --

    def Init(self) -> bool:
        """Set up readers, writers, output channels."""
        self._managed_init()
        logger.info("Initializing ControlComponent v%s ...", self.VERSION)

        self._node = CyberNode("control")

        # Readers
        self._strategy_reader = self._node.CreateReader(
            "/lol/strategy", StrategyAdvice, pending_queue_size=16,
        )
        self._win_pred_reader = self._node.CreateReader(
            "/lol/win_prediction", WinPrediction, pending_queue_size=4,
        )
        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", GameSnapshot, pending_queue_size=4,
        )

        # Writers
        self._status_writer = self._node.CreateWriter(
            "/lol/control_status", StatusMessage,
        )
        self._voice_queue_writer = self._node.CreateWriter(
            "/lol/voice_queue", VoiceCommand,
        )

        # Sub-modules
        self._dispatcher = ActionDispatcher()
        self._overlay = OverlayRenderer()

        # Register output channels
        voice_ch = VoiceOutputChannel(
            voice_writer=self._voice_queue_writer,
            cooldown_s=5.0,
        )
        overlay_ch = OverlayOutputChannel(
            renderer=self._overlay,
            cooldown_s=2.0,
        )
        log_ch = LogOutputChannel()

        self.register_channel(voice_ch)
        self.register_channel(overlay_ch)
        self.register_channel(log_ch)

        # Claude19: VoicePriorityQueue + GameNarrator
        self._voice_priority_queue = VoicePriorityQueue()
        self._game_narrator = GameNarrator()

        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info("ControlComponent initialized (%d channels)",
                     len(self._channels))
        return True

    def Proc(self) -> bool:
        """Apollo-style 3-step Proc().

        Step 1: Drain inputs (strategy advice + win prediction)
        Step 2: Dispatch actions through all registered channels
        Step 3: Flush overlay + publish status
        """
        if self.should_skip_proc():
            return True

        with self.measure_proc() as m:
            self._tick_count += 1

            # Step 1: Drain strategy advice
            self._drain_inputs()

            # Step 2: Dispatch pending actions
            self._dispatch_pending()

            # Step 3: Flush channels
            self._flush_channels()

            m.success = True
            return True

    def on_shutdown(self) -> None:
        """Graceful shutdown."""
        self._managed_shutdown()
        for ch in self._channels.values():
            try:
                ch.shutdown()
            except Exception:
                pass
        if self._node:
            self._node.shutdown()

    # -- Channel management --

    def register_channel(self, channel: OutputChannel) -> None:
        """Register an output channel."""
        self._channels[channel.name] = channel
        logger.debug("Registered output channel: %s", channel.name)

    def unregister_channel(self, name: str) -> Optional[OutputChannel]:
        """Unregister and return an output channel."""
        return self._channels.pop(name, None)

    def get_channel(self, name: str) -> Optional[OutputChannel]:
        """Get a registered channel by name."""
        return self._channels.get(name)

    # -- Internal Proc steps --

    def _drain_inputs(self) -> None:
        """Step 1: Read all pending strategy and prediction messages.

        Claude19: Strategy advice voice text now routes through
        VoicePriorityQueue for priority ordering and category cooldowns,
        replacing the simple per-action dedup_key cooldown.
        """
        # Strategy advice
        if self._strategy_reader:
            advices = self._strategy_reader.drain()
            for advice in advices:
                action = self._advice_to_action(advice)
                if action:
                    self._pending_actions.append(action)
                    # Claude19: Also enqueue into VoicePriorityQueue
                    if self._voice_priority_queue and action.voice_text:
                        prio = VoicePriority.HIGH if action.priority.value <= 1 else VoicePriority.MEDIUM
                        self._voice_priority_queue.enqueue(
                            action.voice_text,
                            category=action.source or "strategy",
                            priority=prio,
                            game_time=action.data.get("game_time", 0.0) if action.data else 0.0,
                        )

        # Win probability periodic announcement
        if self._win_pred_reader:
            self._win_pred_reader.Observe()
            win_pred = self._win_pred_reader.GetLatestObserved()
            if win_pred is not None:
                win_action = self._maybe_win_announce(win_pred)
                if win_action:
                    self._pending_actions.append(win_action)
                self._update_overlay_win_prob(win_pred)

                # Claude19: Generate natural language narration for win updates
                if self._game_narrator:
                    lines = self._game_narrator.narrate_win_update(
                        win_pred.blue_win_prob,
                        self._last_narrated_win_prob,
                        "BLUE",
                        win_pred.game_time,
                    )
                    for line in lines:
                        if self._voice_priority_queue:
                            self._voice_priority_queue.enqueue(
                                line.text,
                                category=line.category,
                                priority=VoicePriority(min(line.priority, 3)),
                                game_time=line.game_time,
                            )
                    self._last_narrated_win_prob = win_pred.blue_win_prob

    def _dispatch_pending(self) -> None:
        """Step 2: Route pending actions through all channels."""
        while self._pending_actions:
            action = self._pending_actions.popleft()
            dispatched = False
            for ch in self._channels.values():
                if ch.dispatch(action):
                    dispatched = True
            if dispatched:
                self._dispatch_count += 1

    def _flush_channels(self) -> None:
        """Step 3: Flush all channels (overlay expiration etc)."""
        for ch in self._channels.values():
            try:
                ch.flush()
            except Exception as exc:
                logger.debug("Channel %s flush error: %s",
                             ch.name, exc)

    # -- Action mapping --

    def _advice_to_action(
        self, advice: StrategyAdvice,
    ) -> Optional[DispatchAction]:
        """Convert StrategyAdvice to DispatchAction.

        Claude16: Fixed to use StrategyAdvice's actual fields
        (primary_action, reasoning, urgency) instead of removed
        rec_type/text/priority. Removed game_time (not in DispatchAction).
        """
        if not advice.reasoning:
            return None

        if advice.urgency >= 0.8:
            prio = ActionPriority.CRITICAL
        elif advice.urgency >= 0.6:
            prio = ActionPriority.HIGH
        elif advice.urgency >= 0.3:
            prio = ActionPriority.MEDIUM
        else:
            prio = ActionPriority.LOW

        return DispatchAction(
            category=ActionCategory.STRATEGY_ADVICE,
            priority=prio,
            text=advice.reasoning,
            voice_text=advice.reasoning,
            source=f"planning.{advice.primary_action}",
            dedup_key=f"strategy:{advice.primary_action}",
            data={"game_time": advice.game_time},
        )

    def _maybe_win_announce(
        self, win_pred: WinPrediction,
    ) -> Optional[DispatchAction]:
        """Generate win probability voice action if due."""
        now = time.monotonic()
        elapsed = now - self._last_win_announce_time
        prob = win_pred.blue_win_prob
        prob_change = abs(prob - self._last_announced_win_prob)

        should_announce = (
            elapsed >= _WIN_PROB_ANNOUNCE_INTERVAL_S
            or prob_change >= _WIN_PROB_CHANGE_THRESHOLD
        )
        if not should_announce:
            return None

        self._last_win_announce_time = now
        self._last_announced_win_prob = prob

        pct = round(max(prob, 1.0 - prob) * 100)
        side = "our" if prob > 0.5 else "enemy"
        text = f"Win probability update: {pct}% in {side} favor."

        return DispatchAction(
            category=ActionCategory.STRATEGY_ADVICE,
            priority=ActionPriority.MEDIUM,
            text=text,
            voice_text=text,
            source="prediction.win_probability",
            dedup_key="win_prob_announce",
            data={"game_time": win_pred.game_time},
        )

    def _update_overlay_win_prob(
        self, win_pred: WinPrediction,
    ) -> None:
        """Update HUD overlay with current win probability."""
        if self._overlay is None:
            return
        try:
            self._overlay.show_win_probability(
                win_pred.blue_win_prob,
                win_pred.confidence,
            )
        except Exception:
            pass

    # -- Status --

    def control_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "tick_count": self._tick_count,
            "dispatch_count": self._dispatch_count,
            "last_win_prob": self._last_announced_win_prob,
            "pending_actions": len(self._pending_actions),
            "channels": {
                name: ch.stats.snapshot()
                for name, ch in self._channels.items()
            },
        })
        return base


# Claude26: SafetyGuard moved to modules/control/dispatch/safety_guard.py
# Claude26: CooldownTracker moved to modules/control/dispatch/cooldown_tracker.py
# Claude26: DedupFilter moved to modules/control/dispatch/dedup_filter.py
# Claude26: DispatchRateLimiter moved to modules/control/dispatch/rate_limiter.py
# Claude26: ActionEffectivenessTracker moved to modules/control/dispatch/effectiveness_tracker.py
    # ─── Apollo command freshness check (Claude23) ───────────────────────
    #
    # Apollo canbus_component.cc:218-275 OnControlCommandCheck():
    # Checks if incoming commands are stale (cmd_time_diff > threshold).
    # If stale, triggers ProcessGuardianCmdTimeout() → estop.
    #
    # Control is the output layer — if planning advice is stale,
    # we should suppress voice announcements to avoid misleading the player.

    _CMD_FRESHNESS_THRESHOLD_S: float = 15.0  # planning runs at 2Hz, 15s is generous

    def _check_command_freshness(self, advice: Any) -> bool:
        """Check if planning advice is fresh enough to act on.

        Apollo equivalent: OnControlCommandCheck() time-delay detection.
        Stale advice = wrong advice. Better to say nothing than mislead.

        Returns True if advice is fresh enough to announce/display.
        """
        if advice is None:
            return False

        # Check advice timestamp
        advice_time = 0.0
        if hasattr(advice, "timestamp"):
            advice_time = advice.timestamp
        elif isinstance(advice, dict):
            advice_time = advice.get("timestamp", 0.0)

        if advice_time <= 0:
            return True  # no timestamp = legacy format, pass through

        import time as _time
        age = _time.time() - advice_time
        if age > self._CMD_FRESHNESS_THRESHOLD_S:
            logger.warning(
                "Planning advice is stale: age=%.1f s > threshold=%.1f s",
                age, self._CMD_FRESHNESS_THRESHOLD_S,
            )
            return False
        return True

    def _throttle_on_safe_mode(self) -> bool:
        """Check SafeMode and throttle output if active.

        Apollo equivalent: ProcessGuardianCmdTimeout() sets throttle=0.
        When SafeMode is active, suppress new voice announcements.

        Returns True if output should be suppressed.
        """
        try:
            from modules.common.component_base import SafeMode
            safe = SafeMode.instance()
            if safe.is_active:
                logger.debug(
                    "SafeMode active — throttling control output: %s",
                    safe.active_sources,
                )
                return True
        except ImportError:
            pass
        return False
