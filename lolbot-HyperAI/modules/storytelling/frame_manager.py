"""
FrameManager — Apollo frame_manager.cc equivalent.
=====================================================
lolbot-HyperAI · modules/storytelling

查看 Apollo ``modules/storytelling/frame_manager.cc`` 上现有
``FrameManager`` 的实现方式, 理解其模式, 特别是 ``StartFrame()`` →
``EndFrame()`` 帧生命周期和 MonitorLogBuffer 的设计。从 Apollo
FrameManager 这个好例子开始。然后, 遵循该模式实现一个新的
``FrameManager``, 让 StorytellingComponent 可以在每个 Proc() 周期
调用 StartFrame/EndFrame, 并能自动管理 teller 的帧数据和日志发布。

位置: lolbot-HyperAI/modules/storytelling/frame_manager.py

Apollo reference:
    modules/storytelling/frame_manager.cc — StartFrame() + EndFrame()
    modules/storytelling/frame_manager.h — class definition
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from modules.storytelling.story_tellers.base_teller import (
    BaseTeller,
    GameContext,
    GameEvent,
    NarrationSegment,
)

logger = logging.getLogger(__name__)


class FrameManager:
    """Manages the per-Proc() frame lifecycle for storytelling.

    Apollo equivalent: ``FrameManager`` in frame_manager.cc.

    Each Proc() call:
        1. StartFrame() — observe channels, collect events
        2. Process tellers — each teller generates narrations
        3. EndFrame() — publish narrations, flush logs

    The FrameManager owns the registered tellers and coordinates
    their execution within the frame boundary.

    Usage::

        fm = FrameManager(node)
        fm.register_teller(TeamfightTeller())
        fm.register_teller(ObjectiveTeller())

        # In Proc():
        fm.start_frame()
        narrations = fm.process_frame(events, context)
        fm.end_frame()
    """

    def __init__(self) -> None:
        self._tellers: List[BaseTeller] = []
        self._frame_count: int = 0
        self._total_narrations: int = 0
        self._frame_start_time: float = 0.0
        self._last_frame_latency_ms: float = 0.0

        # Per-frame accumulation
        self._frame_narrations: List[NarrationSegment] = []
        self._frame_events_processed: int = 0

    def register_teller(self, teller: BaseTeller) -> None:
        """Register a story teller.

        Apollo equivalent: FrameManager constructor registers story
        tellers from config.
        """
        self._tellers.append(teller)
        logger.info(
            "Registered teller: %s (handles: %s)",
            teller.name(),
            sorted(teller.handled_event_types()),
        )

    def start_frame(self) -> None:
        """Begin a new storytelling frame.

        Apollo equivalent: ``FrameManager::StartFrame()`` — calls
        ``node_->Observe()`` to snapshot current channel state.
        """
        self._frame_start_time = time.monotonic()
        self._frame_narrations.clear()
        self._frame_events_processed = 0
        self._frame_count += 1

    def process_frame(
        self,
        events: List[GameEvent],
        context: GameContext,
    ) -> List[NarrationSegment]:
        """Run all tellers against the current frame's events.

        Apollo equivalent: iterating story_tellers in Storytelling::Proc()
        and collecting Stories output.

        Returns all narration segments produced in priority order.
        """
        self._frame_events_processed = len(events)

        for teller in self._tellers:
            try:
                segments = teller.process(events, context)
                self._frame_narrations.extend(segments)
            except Exception as exc:
                logger.error(
                    "Teller '%s' raised exception: %s",
                    teller.name(), exc,
                )

        # Sort by priority (highest first)
        self._frame_narrations.sort(
            key=lambda s: s.priority.value, reverse=True
        )

        self._total_narrations += len(self._frame_narrations)
        return list(self._frame_narrations)

    def end_frame(self) -> None:
        """End the current storytelling frame.

        Apollo equivalent: ``FrameManager::EndFrame()`` — publishes
        monitor logs via ``log_buffer_.Publish()``.
        """
        self._last_frame_latency_ms = (
            (time.monotonic() - self._frame_start_time) * 1000.0
        )

        if self._frame_narrations and logger.isEnabledFor(logging.DEBUG):
            for seg in self._frame_narrations:
                logger.debug(
                    "Narration [%s/%s]: %s",
                    seg.teller_name, seg.tone.name, seg.text,
                )

    @property
    def teller_count(self) -> int:
        return len(self._tellers)

    def stats(self) -> Dict[str, Any]:
        """Export frame manager statistics."""
        return {
            "frame_count": self._frame_count,
            "total_narrations": self._total_narrations,
            "teller_count": len(self._tellers),
            "last_frame_latency_ms": round(self._last_frame_latency_ms, 3),
            "last_frame_events": self._frame_events_processed,
            "last_frame_narrations": len(self._frame_narrations),
            "tellers": {
                t.name(): t.stats() for t in self._tellers
            },
        }
