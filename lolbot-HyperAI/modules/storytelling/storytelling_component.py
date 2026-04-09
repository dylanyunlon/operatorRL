"""
StorytellingComponent — Apollo storytelling.cc equivalent.
=============================================================
lolbot-HyperAI · modules/storytelling

查看 Apollo ``modules/storytelling/storytelling.cc`` 上现有
``Storytelling`` 组件的实现方式, 理解其模式, 特别是 Init() 注册
story_tellers + Proc() 调用 FrameManager::StartFrame/EndFrame + 发布
Stories 的设计。从 Apollo Storytelling 这个好例子开始。然后, 遵循该模式
实现一个新的 ``StorytellingComponent``, 让系统可以独立运行叙事生成,
并能通过 FrameManager 协调所有 teller。接着引入 channel 读写, 使叙事
结果发布到 /lol/narration 供 control 的 voice 模块消费。

位置: lolbot-HyperAI/modules/storytelling/storytelling_component.py

Apollo reference:
    modules/storytelling/storytelling.cc — Storytelling::Init() + Proc()
    modules/storytelling/storytelling.h — class definition
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from cyber.component.timer_component import (
    ComponentConfig,
    TimerComponent,
)
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.component_base import (
    ComponentDependency,
    LifecycleState,
    ManagedComponent,
)
from modules.storytelling.frame_manager import FrameManager
from modules.storytelling.story_tellers.base_teller import (
    GameContext,
    GameEvent,
    NarrationSegment,
)
from modules.storytelling.story_tellers.teamfight_teller import TeamfightTeller
from modules.storytelling.story_tellers.objective_teller import ObjectiveTeller
from modules.storytelling.common.storytelling_gflags import StorytellingFlags

logger = get_logger("storytelling")

# ── Constants ────────────────────────────────────────────────────────────────

_STORYTELLING_INTERVAL_MS = 1000.0  # 1Hz
_NARRATION_CHANNEL = "/lol/narration"
_EVENT_CHANNEL = "/lol/events"
_STATE_CHANNEL = "/lol/game_state"
_WIN_PRED_CHANNEL = "/lol/win_prediction"


class StorytellingComponent(TimerComponent, ManagedComponent):
    """Apollo-style storytelling component: 1Hz narration generation.

    Apollo equivalent: ``Storytelling`` class in storytelling.cc.

    Reads game events and state from channels, runs registered
    story tellers through the FrameManager, and publishes
    narration segments for the control/voice pipeline.

    Pipeline position::

        perception → events →  [StorytellingComponent]  → narration → control/voice
                  → state  →         ↑                  →
                              FrameManager + Tellers
    """

    COMPONENT_NAME = "storytelling"
    DEPENDENCIES = [
        ComponentDependency("perception", required=True),
    ]
    VERSION = "1.0.0"
    CB_MAX_FAILURES = 5
    CB_COOLDOWN_S = 3.0

    def __init__(
        self,
        flags: Optional[StorytellingFlags] = None,
    ) -> None:
        self._flags = flags or StorytellingFlags()
        super().__init__(
            config=ComponentConfig(
                name="storytelling",
                interval_ms=self._flags.storytelling_interval_ms,
                warn_threshold_ms=self._flags.storytelling_interval_ms * 2,
                max_consecutive_failures=self.CB_MAX_FAILURES,
            ),
        )

        self._frame_manager: Optional[FrameManager] = None
        self._node: Optional[CyberNode] = None

        # Readers
        self._event_reader: Optional[Reader] = None
        self._state_reader: Optional[Reader] = None
        self._win_pred_reader: Optional[Reader] = None

        # Writer
        self._narration_writer: Optional[Writer] = None

        # Stats
        self._proc_count: int = 0
        self._narrations_published: int = 0

    def Init(self) -> bool:
        """Initialize storytelling component.

        Apollo equivalent: ``Storytelling::Init()`` — creates
        FrameManager, registers tellers, creates readers/writers.
        """
        self._managed_init()
        logger.info("Initializing StorytellingComponent v%s ...", self.VERSION)

        # ── Step 1: Create FrameManager (Apollo: FrameManager(node)) ────
        self._frame_manager = FrameManager()

        # ── Step 2: Register story tellers (Apollo: register stories) ────
        if self._flags.enable_teamfight_teller:
            self._frame_manager.register_teller(
                TeamfightTeller(cooldown_s=self._flags.same_type_cooldown_s)
            )
        if self._flags.enable_objective_teller:
            self._frame_manager.register_teller(
                ObjectiveTeller(cooldown_s=self._flags.same_type_cooldown_s / 2)
            )

        logger.info(
            "Registered %d tellers", self._frame_manager.teller_count
        )

        # ── Step 3: Create cyber readers/writers ─────────────────────────
        self._node = CyberNode("storytelling")

        self._event_reader = self._node.CreateReader(
            _EVENT_CHANNEL, dict
        )
        self._state_reader = self._node.CreateReader(
            _STATE_CHANNEL, dict
        )
        self._win_pred_reader = self._node.CreateReader(
            _WIN_PRED_CHANNEL, dict
        )
        self._narration_writer = self._node.CreateWriter(
            _NARRATION_CHANNEL, dict
        )

        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info("StorytellingComponent initialized")
        return True

    def Proc(self) -> bool:
        """Execute one storytelling frame.

        Apollo equivalent: ``Storytelling::Proc()``
            1. StartFrame()
            2. Process stories
            3. EndFrame()
        """
        if self.should_skip_proc():
            return True

        self._proc_count += 1

        with self.measure_proc() as m:
            # ── Apollo: FrameManager::StartFrame() ───────────────────
            self._frame_manager.start_frame()

            # ── Observe channels ─────────────────────────────────────
            events = self._read_events()
            context = self._build_context()

            # ── Process all tellers ──────────────────────────────────
            narrations = self._frame_manager.process_frame(events, context)

            # ── Publish narrations ───────────────────────────────────
            for segment in narrations:
                self._publish_narration(segment)

            # ── Apollo: FrameManager::EndFrame() ─────────────────────
            self._frame_manager.end_frame()

            m.success = True

        return True

    def on_shutdown(self) -> None:
        """Shutdown storytelling component."""
        self._managed_shutdown()
        if self._node:
            self._node.shutdown()
        logger.info("StorytellingComponent shutdown complete")

    # ── Internal methods ─────────────────────────────────────────────────

    def _read_events(self) -> List[GameEvent]:
        """Read pending events from the event channel."""
        if self._event_reader is None:
            return []

        raw = self._event_reader.GetLatestObserved()
        if raw is None:
            return []

        # Convert raw event dicts to GameEvent objects
        events_raw = raw if isinstance(raw, list) else [raw]
        events: List[GameEvent] = []
        for e in events_raw:
            if isinstance(e, dict):
                events.append(GameEvent(
                    event_type=e.get("EventName", e.get("event_type", "")),
                    event_data=e,
                    timestamp=e.get("EventTime", time.time()),
                    event_id=str(e.get("EventID", "")),
                ))
        return events

    def _build_context(self) -> GameContext:
        """Build game context from state and prediction channels."""
        context = GameContext()

        # Read game state
        state = (
            self._state_reader.GetLatestObserved()
            if self._state_reader else None
        )
        if isinstance(state, dict):
            context.game_time = state.get("game_time", 0.0)
            context.gold_diff = state.get("gold_diff", 0.0)
            context.ally_kills = state.get("ally_kills", 0)
            context.enemy_kills = state.get("enemy_kills", 0)
            context.ally_turrets = state.get("ally_turrets_destroyed", 0)
            context.enemy_turrets = state.get("enemy_turrets_destroyed", 0)
            context.player_champion = state.get("player_champion", "")
            context.is_behind = context.gold_diff < -1000

            # Determine game phase
            gt = context.game_time
            if gt < 900:  # <15 min
                context.game_phase = "early"
            elif gt < 1500:  # <25 min
                context.game_phase = "mid"
            else:
                context.game_phase = "late"

        # Read win prediction
        pred = (
            self._win_pred_reader.GetLatestObserved()
            if self._win_pred_reader else None
        )
        if isinstance(pred, dict):
            context.win_probability = pred.get("win_probability", 0.5)

        return context

    def _publish_narration(self, segment: NarrationSegment) -> None:
        """Publish a narration segment to the narration channel."""
        if self._narration_writer is None:
            return
        self._narration_writer.Write(segment.to_dict())
        self._narrations_published += 1

    # ── Introspection ────────────────────────────────────────────────────

    def storytelling_status(self) -> Dict[str, Any]:
        """Extended status for monitoring."""
        base = self.status()
        base.update({
            "proc_count": self._proc_count,
            "narrations_published": self._narrations_published,
            "frame_manager": (
                self._frame_manager.stats()
                if self._frame_manager else {}
            ),
        })
        return base
