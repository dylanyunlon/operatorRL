"""
ControlComponent — Unified output dispatch (voice + overlay + log).
====================================================================
lolbot-HyperAI · Control Layer

The Apollo ``control`` analog: reads strategy decisions from the planning
pipeline and dispatches them to voice, overlay, and structured log outputs.

Architecture position:
    modules/control/control_component.py   ← YOU ARE HERE
    ├─ Reads: /lol/strategy (StrategyAdvice from planning)
    ├─ Reads: /lol/macro_decision (MacroDecision from planning)
    ├─ Reads: /lol/win_prediction (WinPrediction from prediction)
    ├─ Reads: /lol/teamfight_assessment (from prediction)
    ├─ Delegates to: ActionDispatcher, OverlayRenderer, VoiceNarrator
    ├─ Publishes: /lol/control_status (StatusMessage)
    └─ Publishes: /lol/voice_queue (VoiceCommand for downstream TTS)

Apollo reference:
    modules/control/control_component.cc — ``Proc()``
    modules/control/controller_agent.cc  — dispatch to subcontrollers

Design notes:
    - 5Hz tick (200ms) — faster than planning to flush queued actions
    - ActionDispatcher handles dedup, priority, and channel routing
    - OverlayRenderer manages HUD elements with TTL and priority eviction
    - VoiceNarrator queues TTS with rate limiting
    - All sub-module failures are non-fatal and logged
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
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
from modules.control.overlay.overlay_renderer import (
    OverlayRenderer,
)
from modules.control.voice_output.voice_narrator import (
    VoiceNarratorComponent as VoiceNarrator,
)

logger = get_logger("control")

# ─── Constants ───────────────────────────────────────────────────────────────

_CONTROL_INTERVAL_MS = 200.0    # 5Hz — flush outputs fast
_WARN_THRESHOLD_MS = 150.0
_WIN_PROB_ANNOUNCE_INTERVAL_S = 30.0  # voice announce win% every 30s
_WIN_PROB_CHANGE_THRESHOLD = 0.10     # announce if win% changes > 10%


class ControlComponent(TimerComponent):
    """Control component: dispatches strategy to voice/overlay/log.

    Each Proc() cycle:
    1. Reads latest StrategyAdvice from /lol/strategy
    2. Reads latest MacroDecision from /lol/macro_decision
    3. Reads latest WinPrediction for periodic voice update
    4. Routes through ActionDispatcher → voice + overlay + log
    5. Updates OverlayRenderer with current win probability HUD
    """

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

        # Sub-modules
        self._dispatcher: Optional[ActionDispatcher] = None
        self._overlay: Optional[OverlayRenderer] = None
        self._narrator: Optional[VoiceNarrator] = None

        # State
        self._tick_count: int = 0
        self._dispatch_count: int = 0
        self._last_win_announce_time: float = 0.0
        self._last_announced_win_prob: float = 0.5

    def Init(self) -> bool:
        """Set up readers, writers, and sub-modules."""
        logger.info("Initializing ControlComponent...")

        self._node = CyberNode("control")

        self._strategy_reader = self._node.CreateReader(
            "/lol/strategy", StrategyAdvice, pending_queue_size=16,
        )
        self._win_pred_reader = self._node.CreateReader(
            "/lol/win_prediction", WinPrediction, pending_queue_size=4,
        )
        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", GameSnapshot, pending_queue_size=4,
        )

        self._status_writer = self._node.CreateWriter(
            "/lol/control_status", StatusMessage,
        )
        self._voice_queue_writer = self._node.CreateWriter(
            "/lol/voice_queue", VoiceCommand,
        )

        # Instantiate sub-modules
        self._dispatcher = ActionDispatcher()
        self._overlay = OverlayRenderer()
        # Note: VoiceNarratorComponent is a TimerComponent with its own
        # Proc() loop — it runs independently via mainboard. We only
        # instantiate it here to have a reference for status queries.
        # Voice dispatch is done via the /lol/voice_queue channel.
        self._narrator = None  # managed by mainboard, not by us

        logger.info("ControlComponent initialized")
        return True

    def Proc(self) -> bool:
        """One control dispatch cycle."""
        self._tick_count += 1

        # ── Drain strategy advice queue ──────────────────────────────
        if self._strategy_reader:
            advices = self._strategy_reader.drain()
            for advice in advices:
                self._dispatch_strategy(advice)

        # ── Periodic win probability announcement ────────────────────
        if self._win_pred_reader:
            self._win_pred_reader.Observe()
            win_pred = self._win_pred_reader.GetLatestObserved()
            if win_pred is not None:
                self._maybe_announce_win_prob(win_pred)
                self._update_overlay_win_prob(win_pred)

        # ── Process overlay expiration ───────────────────────────────
        if self._overlay:
            try:
                self._overlay.process_pending()
            except Exception as exc:
                logger.warning("Overlay process error: %s", exc)

        return True

    def _dispatch_strategy(self, advice: StrategyAdvice) -> None:
        """Route a strategy advice through the dispatcher."""
        if self._dispatcher is None:
            return

        # Map StrategyAdvice priority to ActionPriority
        if advice.priority >= 3:
            prio = ActionPriority.CRITICAL
        elif advice.priority >= 2:
            prio = ActionPriority.HIGH
        elif advice.priority >= 1:
            prio = ActionPriority.MEDIUM
        else:
            prio = ActionPriority.LOW

        action = DispatchAction(
            category=ActionCategory.STRATEGY_ADVICE,
            priority=prio,
            text=advice.text,
            voice_text=advice.text,
            source=f"planning.{advice.rec_type}",
            dedup_key=f"strategy:{advice.rec_type}",
            game_time=advice.game_time,
        )

        try:
            result = self._dispatcher.dispatch(action)
            self._dispatch_count += 1

            if result.get("voice"):
                logger.info("Voiced: %s", advice.text[:60])
        except Exception as exc:
            logger.warning("Dispatch error: %s", exc)

    def _maybe_announce_win_prob(self, win_pred: WinPrediction) -> None:
        """Announce win probability via voice at periodic intervals."""
        now = time.monotonic()
        elapsed = now - self._last_win_announce_time
        prob = win_pred.blue_win_prob

        # Announce if enough time elapsed OR big swing
        prob_change = abs(prob - self._last_announced_win_prob)
        should_announce = (
            elapsed >= _WIN_PROB_ANNOUNCE_INTERVAL_S
            or prob_change >= _WIN_PROB_CHANGE_THRESHOLD
        )

        if not should_announce:
            return

        self._last_win_announce_time = now
        self._last_announced_win_prob = prob

        # Format for voice
        pct = round(max(prob, 1.0 - prob) * 100)
        side = "our" if prob > 0.5 else "enemy"
        text = f"Win probability update: {pct}% in {side} favor."

        if self._voice_queue_writer:
            cmd = VoiceCommand(
                text=text,
                priority=1,
                game_time=win_pred.game_time,
            )
            self._voice_queue_writer.Write(cmd)

    def _update_overlay_win_prob(self, win_pred: WinPrediction) -> None:
        """Update the HUD overlay with current win probability."""
        if self._overlay is None:
            return
        try:
            self._overlay.show_win_probability(
                win_pred.blue_win_prob,
                win_pred.confidence,
            )
        except Exception:
            pass  # overlay is best-effort

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()
        if self._dispatcher:
            try:
                self._dispatcher.flush()
            except Exception:
                pass

    def control_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "tick_count": self._tick_count,
            "dispatch_count": self._dispatch_count,
            "last_win_prob": self._last_announced_win_prob,
        })
        return base
