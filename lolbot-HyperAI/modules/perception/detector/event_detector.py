"""
modules/perception/detector/event_detector.py
===============================================
从 Apollo ``perception/camera_detection_multi_stage/detector/`` 这个好例子开始——
它将检测逻辑从 component.cc 分离到 ``detector/`` 子目录。然后, 遵循该模式
实现 ``EventDetector``, 让 ``PerceptionComponent`` 可以单行调用完成事件检测,
并能独立测试事件去重逻辑。接着引入 event rate tracking, 使 monitor 能够
检测感知丢失, 同时优化去重用 set 查找效率(O(1))。随后整合 data quality
scoring, 令 prediction 支持置信度加权, 进而增强预测准确性。最终完善
anomaly detection, 确保极端数据(gold>15k)兼容正常数据流, 全面升级
perception 可靠性以达成 Apollo 级别的检测/组件分离目标。

Extracted verbatim from PerceptionComponent (Claude25 b0178fa):
    _detect_new_events()         → detect_new()
    get_event_rates()            → event_rates()
    compute_data_quality_score() → data_quality_score()
    detect_anomalies()           → detect_anomalies()
    _validate_input()            → validate_input()
    _check_upstream_health()     → check_upstream_health()
Zero logic changes.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    GameSnapshot,
)

logger = get_logger("perception.detector")


class EventDetector:
    """In-game event detection and quality analysis.

    Apollo equivalent: detector/ sub-modules in perception.
    Thread-safety: NOT thread-safe. Called from PerceptionComponent.Proc() only.
    """

    def __init__(self) -> None:
        self._seen_event_ids: Set[int] = set()
        self._all_events: List[GameEvent] = []
        self._last_processed_game_time: float = 0.0

    # ─── Event Detection (verbatim from Claude25) ────────────────────

    def detect_new(self, data: Dict[str, Any]) -> List[GameEvent]:
        """Detect events not seen in previous ticks. Uses event ID for dedup."""
        events_wrapper = data.get("events", {})
        raw_events = events_wrapper.get("Events", [])
        new_events: List[GameEvent] = []

        for evt_raw in raw_events:
            evt_id = evt_raw.get("EventID", 0)
            if evt_id in self._seen_event_ids:
                continue
            self._seen_event_ids.add(evt_id)

            evt_name = evt_raw.get("EventName", "")
            try:
                evt_type = EventType(evt_name)
            except ValueError:
                evt_type = EventType.GAME_START

            event = GameEvent(
                event_id=evt_id,
                event_type=evt_type,
                game_time=evt_raw.get("EventTime", 0.0),
                killer=evt_raw.get("KillerName", ""),
                victim=evt_raw.get("VictimName", ""),
                assisters=tuple(evt_raw.get("Assisters", [])),
            )
            new_events.append(event)
            self._all_events.append(event)

        return new_events

    # ─── Event Rate Tracking (Claude17, verbatim) ────────────────────

    def event_rates(self, window_s: float = 60.0) -> Dict[str, float]:
        """Compute per-minute event rates by type."""
        now = time.time()
        cutoff = now - window_s
        recent = [
            e for e in self._all_events
            if hasattr(e, 'timestamp') and e.timestamp > cutoff
        ]
        counts: Dict[str, int] = {}
        for e in recent:
            etype = getattr(e, 'event_type', 'unknown')
            if hasattr(etype, 'value'):
                etype = etype.value
            counts[etype] = counts.get(etype, 0) + 1
        minutes = max(window_s / 60.0, 1.0 / 60.0)
        return {k: round(v / minutes, 2) for k, v in counts.items()}

    # ─── Data Quality (Claude17, verbatim) ───────────────────────────

    def data_quality_score(self, snapshot: Optional[GameSnapshot]) -> float:
        """Score snapshot quality 0.0–1.0."""
        if snapshot is None:
            return 0.0
        score = 0.0
        checks = 0

        checks += 1
        if hasattr(snapshot, 'blue_team') and hasattr(snapshot, 'red_team'):
            blue_count = len(getattr(snapshot.blue_team, 'players', []))
            red_count = len(getattr(snapshot.red_team, 'players', []))
            if blue_count == 5 and red_count == 5:
                score += 1.0
            elif blue_count + red_count > 0:
                score += 0.5

        checks += 1
        if hasattr(snapshot, 'game_time') and snapshot.game_time > 0:
            score += 1.0

        checks += 1
        if hasattr(snapshot, 'phase') and snapshot.phase is not None:
            score += 1.0

        checks += 1
        if hasattr(snapshot, 'gold_diff'):
            score += 1.0

        return round(score / max(checks, 1), 4) if checks > 0 else 0.0

    # ─── Anomaly Detection (Claude17, verbatim) ──────────────────────

    def detect_anomalies(self, snapshot: Optional[GameSnapshot]) -> List[Dict[str, Any]]:
        """Detect anomalous patterns in perception data."""
        anomalies: List[Dict[str, Any]] = []
        if snapshot is None:
            return anomalies
        if hasattr(snapshot, 'gold_diff'):
            if abs(snapshot.gold_diff) > 15000:
                anomalies.append({
                    "type": "extreme_gold_diff",
                    "value": snapshot.gold_diff,
                    "threshold": 15000,
                    "game_time": getattr(snapshot, 'game_time', 0),
                })
        return anomalies

    # ─── Input Validation (Claude23, verbatim) ───────────────────────

    def validate_input(self, allgamedata: Dict[str, Any]) -> bool:
        """Validate input data before processing."""
        if not isinstance(allgamedata, dict):
            logger.warning("Input is not a dict: %s", type(allgamedata).__name__)
            return False
        required = ("allPlayers", "gameData")
        for key in required:
            if key not in allgamedata:
                logger.warning("Input missing required key: %r", key)
                return False
        players = allgamedata.get("allPlayers")
        if not isinstance(players, list) or len(players) == 0:
            return False
        game_data = allgamedata.get("gameData", {})
        game_time = game_data.get("gameTime", 0.0)
        if game_time <= 0:
            return False
        if game_time == self._last_processed_game_time:
            return False
        self._last_processed_game_time = game_time
        return True

    def check_upstream_health(self, reader: Any) -> bool:
        """Check if canbus upstream is providing fresh data. (Claude23)"""
        if reader is None:
            return True
        if hasattr(reader, "is_stale"):
            if reader.is_stale(max_age_s=3.0):
                logger.warning("Upstream canbus data is stale (>3s old)")
                return False
        return True

    # ─── Introspection ───────────────────────────────────────────────

    @property
    def all_events(self) -> List[GameEvent]:
        return self._all_events

    @property
    def seen_event_ids(self) -> Set[int]:
        return self._seen_event_ids
