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

logger = get_logger("control")

# --- Constants ---------------------------------------------------------------

_CONTROL_INTERVAL_MS = 200.0    # 5Hz
_WARN_THRESHOLD_MS = 150.0
_WIN_PROB_ANNOUNCE_INTERVAL_S = 30.0
_WIN_PROB_CHANGE_THRESHOLD = 0.10
_MAX_OUTPUT_QUEUE = 64
_DISPATCH_COOLDOWN_S = 2.0


# --- OutputChannel abstraction -----------------------------------------------

class OutputChannelState(Enum):
    """Output channel health state."""
    ACTIVE = auto()
    DEGRADED = auto()
    DISABLED = auto()


@dataclass
class OutputChannelStats:
    """Per-channel statistics."""
    total_dispatched: int = 0
    total_dropped: int = 0
    total_errors: int = 0
    last_dispatch_time: float = 0.0
    last_error: str = ""
    state: OutputChannelState = OutputChannelState.ACTIVE
    consecutive_errors: int = 0

    def record_dispatch(self) -> None:
        self.total_dispatched += 1
        self.last_dispatch_time = time.monotonic()
        self.consecutive_errors = 0

    def record_error(self, error: str) -> None:
        self.total_errors += 1
        self.consecutive_errors += 1
        self.last_error = error

    def record_drop(self) -> None:
        self.total_dropped += 1

    def snapshot(self) -> Dict[str, Any]:
        return {
            "total_dispatched": self.total_dispatched,
            "total_dropped": self.total_dropped,
            "total_errors": self.total_errors,
            "state": self.state.name,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
        }


class OutputChannel:
    """Abstract output channel for strategy dispatching.

    Subclass and implement _do_output() to add new output targets.
    Channels are registered with ControlComponent at Init time.

    Each channel has:
    - Priority filtering (min_priority)
    - Cooldown enforcement (cooldown_s per category)
    - Error tracking and auto-disable after N consecutive errors
    - Graceful degradation (disabled channels are skipped, not fatal)
    """

    MAX_CONSECUTIVE_ERRORS = 10  # auto-disable after this many

    def __init__(
        self,
        name: str,
        min_priority: ActionPriority = ActionPriority.LOW,
        cooldown_s: float = 0.0,
    ) -> None:
        self.name = name
        self.min_priority = min_priority
        self.cooldown_s = cooldown_s
        self.stats = OutputChannelStats()
        self._cooldown_tracker: Dict[str, float] = {}

    def dispatch(self, action: DispatchAction) -> bool:
        """Dispatch action to this channel. Returns True if accepted."""
        # Priority filter
        if action.priority.value < self.min_priority.value:
            self.stats.record_drop()
            return False

        # Auto-disabled
        if self.stats.state == OutputChannelState.DISABLED:
            self.stats.record_drop()
            return False

        # Cooldown check
        if self.cooldown_s > 0 and action.dedup_key:
            last = self._cooldown_tracker.get(action.dedup_key, 0.0)
            if time.monotonic() - last < self.cooldown_s:
                self.stats.record_drop()
                return False

        # Execute
        try:
            self._do_output(action)
            self.stats.record_dispatch()
            if action.dedup_key:
                self._cooldown_tracker[action.dedup_key] = time.monotonic()

            # Recover from degraded if success
            if self.stats.state == OutputChannelState.DEGRADED:
                self.stats.state = OutputChannelState.ACTIVE
            return True

        except Exception as exc:
            self.stats.record_error(f"{type(exc).__name__}: {exc}")
            # Auto-disable after too many consecutive errors
            if self.stats.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                self.stats.state = OutputChannelState.DISABLED
                logger.warning(
                    "[OutputChannel:%s] auto-disabled after %d errors",
                    self.name, self.stats.consecutive_errors,
                )
            elif self.stats.consecutive_errors >= 3:
                self.stats.state = OutputChannelState.DEGRADED
            return False

    def _do_output(self, action: DispatchAction) -> None:
        """Override in subclass to implement actual output."""
        raise NotImplementedError

    def enable(self) -> None:
        """Re-enable a disabled channel."""
        self.stats.state = OutputChannelState.ACTIVE
        self.stats.consecutive_errors = 0

    def disable(self) -> None:
        """Manually disable this channel."""
        self.stats.state = OutputChannelState.DISABLED

    def flush(self) -> None:
        """Flush any buffered output."""
        pass

    def shutdown(self) -> None:
        """Clean up resources."""
        self.flush()


# --- Built-in output channels ------------------------------------------------

class VoiceOutputChannel(OutputChannel):
    """Voice TTS output channel."""

    def __init__(
        self,
        voice_writer: Optional[Writer] = None,
        cooldown_s: float = 5.0,
    ) -> None:
        super().__init__(
            name="voice",
            min_priority=ActionPriority.MEDIUM,
            cooldown_s=cooldown_s,
        )
        self._writer = voice_writer

    def set_writer(self, writer: Writer) -> None:
        self._writer = writer

    def _do_output(self, action: DispatchAction) -> None:
        if self._writer is None:
            return
        text = action.voice_text or action.text
        if not text:
            return
        cmd = VoiceCommand(
            text=text,
            priority=action.priority.value,
            game_time=action.game_time,
        )
        self._writer.Write(cmd)


class OverlayOutputChannel(OutputChannel):
    """HUD overlay output channel."""

    def __init__(
        self,
        renderer: Optional[OverlayRenderer] = None,
        cooldown_s: float = 2.0,
    ) -> None:
        super().__init__(
            name="overlay",
            min_priority=ActionPriority.LOW,
            cooldown_s=cooldown_s,
        )
        self._renderer = renderer

    def set_renderer(self, renderer: OverlayRenderer) -> None:
        self._renderer = renderer

    def _do_output(self, action: DispatchAction) -> None:
        if self._renderer is None:
            return
        self._renderer.add_notification(
            text=action.text,
            priority=action.priority.value,
            source=action.source,
        )

    def flush(self) -> None:
        if self._renderer:
            try:
                self._renderer.process_pending()
            except Exception:
                pass


class LogOutputChannel(OutputChannel):
    """Structured log output channel (always active)."""

    def __init__(self) -> None:
        super().__init__(
            name="log",
            min_priority=ActionPriority.LOW,
            cooldown_s=0.0,
        )
        self._log_buffer: Deque[Dict[str, Any]] = deque(
            maxlen=500,
        )

    def _do_output(self, action: DispatchAction) -> None:
        entry = {
            "ts": time.time(),
            "category": action.category.value
            if hasattr(action.category, "value") else str(action.category),
            "priority": action.priority.name,
            "text": action.text[:200],
            "source": action.source,
            "game_time": action.game_time,
        }
        self._log_buffer.append(entry)
        logger.info(
            "[dispatch] [%s] %s: %s",
            action.priority.name,
            action.source,
            action.text[:80],
        )

    def recent_entries(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get recent log entries."""
        items = list(self._log_buffer)
        return items[-count:]


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
        """Step 1: Read all pending strategy and prediction messages."""
        # Strategy advice
        if self._strategy_reader:
            advices = self._strategy_reader.drain()
            for advice in advices:
                action = self._advice_to_action(advice)
                if action:
                    self._pending_actions.append(action)

        # Win probability periodic announcement
        if self._win_pred_reader:
            self._win_pred_reader.Observe()
            win_pred = self._win_pred_reader.GetLatestObserved()
            if win_pred is not None:
                win_action = self._maybe_win_announce(win_pred)
                if win_action:
                    self._pending_actions.append(win_action)
                self._update_overlay_win_prob(win_pred)

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
        """Convert StrategyAdvice to DispatchAction."""
        if not advice.text:
            return None

        if advice.priority >= 3:
            prio = ActionPriority.CRITICAL
        elif advice.priority >= 2:
            prio = ActionPriority.HIGH
        elif advice.priority >= 1:
            prio = ActionPriority.MEDIUM
        else:
            prio = ActionPriority.LOW

        return DispatchAction(
            category=ActionCategory.STRATEGY_ADVICE,
            priority=prio,
            text=advice.text,
            voice_text=advice.text,
            source=f"planning.{advice.rec_type}",
            dedup_key=f"strategy:{advice.rec_type}",
            game_time=advice.game_time,
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
            game_time=win_pred.game_time,
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
