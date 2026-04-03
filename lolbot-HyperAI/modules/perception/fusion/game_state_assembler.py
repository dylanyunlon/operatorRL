"""
perception/fusion/game_state_assembler.py — 多源数据融合
==========================================================

查看 Apollo modules/perception/fusion/ 上现有多传感器融合的实现方式,
理解其模式, 特别是不同数据源的时间对齐和冲突解决。从 Apollo sensor
fusion pipeline 这个好例子开始。然后遵循该模式实现一个 GameStateAssembler,
让 perception 可以将 LCU Live Client 数据、Fiddler 网络数据、WebSocket
事件流融合为统一的游戏状态快照, 并能处理数据延迟不一致。

位置: lolbot-HyperAI/modules/perception/fusion/game_state_assembler.py
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source metadata
# ---------------------------------------------------------------------------

@dataclass
class SourceFrame:
    """单个数据源的一帧数据.

    Attributes:
        source: 数据源标识 ("lcu", "fiddler", "websocket").
        timestamp: 数据到达时间 (monotonic).
        game_time: 数据中的游戏内时间.
        data: 原始数据.
        latency_ms: 从数据源获取的延迟.
    """
    source: str = ""
    timestamp: float = 0.0
    game_time: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def age_ms(self) -> float:
        """数据年龄 (从到达到现在)."""
        return (time.monotonic() - self.timestamp) * 1000


@dataclass
class FusedSnapshot:
    """融合后的游戏状态快照.

    Attributes:
        game_time: 融合后的游戏时间.
        timestamp: 快照生成时间.
        sources_used: 使用了哪些数据源.
        game_data: 游戏元数据.
        active_player: 活跃玩家数据.
        all_players: 所有玩家数据.
        events: 事件列表.
        quality: 数据质量 [0, 1].
        supplementary: Fiddler/WS 补充数据.
    """
    game_time: float = 0.0
    timestamp: float = field(default_factory=time.time)
    sources_used: List[str] = field(default_factory=list)
    game_data: Dict[str, Any] = field(default_factory=dict)
    active_player: Dict[str, Any] = field(default_factory=dict)
    all_players: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    quality: float = 1.0
    supplementary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time": self.game_time,
            "timestamp": self.timestamp,
            "sources_used": self.sources_used,
            "game_data": self.game_data,
            "active_player": self.active_player,
            "all_players": self.all_players,
            "events": self.events,
            "quality": round(self.quality, 3),
        }


# ---------------------------------------------------------------------------
# Conflict resolution strategies
# ---------------------------------------------------------------------------

class ConflictStrategy:
    """数据冲突解决策略."""

    @staticmethod
    def latest(frames: List[SourceFrame]) -> SourceFrame:
        """选择最新的数据帧."""
        return max(frames, key=lambda f: f.timestamp)

    @staticmethod
    def lowest_latency(frames: List[SourceFrame]) -> SourceFrame:
        """选择延迟最低的数据帧."""
        return min(frames, key=lambda f: f.latency_ms)

    @staticmethod
    def by_priority(
        frames: List[SourceFrame],
        priority: List[str],
    ) -> SourceFrame:
        """按数据源优先级选择.

        Args:
            priority: 优先级列表, 前面的优先.
        """
        for src in priority:
            for f in frames:
                if f.source == src:
                    return f
        return frames[0] if frames else SourceFrame()


# ---------------------------------------------------------------------------
# GameStateAssembler
# ---------------------------------------------------------------------------

# 数据源优先级 (LCU > Fiddler > WebSocket)
_SOURCE_PRIORITY = ["lcu", "fiddler", "websocket"]

# 数据新鲜度阈值 (ms)
_FRESHNESS_THRESHOLD_MS = 2000.0


class GameStateAssembler:
    """游戏状态融合器.

    Apollo 多传感器融合的等价物:
    - LCU Live Client API = 主传感器 (Camera)
    - Fiddler 网络捕获 = 辅助传感器 (Radar)
    - WebSocket 事件流 = 事件传感器 (Ultrasonic)

    融合策略:
    1. LCU 是主数据源, 提供完整游戏状态
    2. Fiddler 补充网络层信息 (如精确的网络事件)
    3. WebSocket 补充实时事件 (如聊天、投降投票)
    4. 冲突时按优先级解决 (LCU > Fiddler > WS)
    5. 过期数据自动丢弃

    Usage::

        assembler = GameStateAssembler()

        # 每个 tick, 喂入各数据源的最新数据:
        assembler.update_source("lcu", lcu_data, latency_ms=15)
        assembler.update_source("fiddler", fiddler_data, latency_ms=50)

        # 融合:
        snapshot = assembler.fuse()
    """

    def __init__(
        self,
        freshness_threshold_ms: float = _FRESHNESS_THRESHOLD_MS,
        source_priority: Optional[List[str]] = None,
    ) -> None:
        self._freshness_threshold_ms = freshness_threshold_ms
        self._source_priority = source_priority or list(_SOURCE_PRIORITY)
        self._frames: Dict[str, SourceFrame] = {}
        self._last_snapshot: Optional[FusedSnapshot] = None
        self._fuse_count: int = 0
        self._seen_event_ids: Set[int] = set()

    def update_source(
        self,
        source: str,
        data: Dict[str, Any],
        latency_ms: float = 0.0,
    ) -> None:
        """更新数据源的最新帧.

        Args:
            source: 数据源标识.
            data: 原始数据 (allgamedata 格式或其他).
            latency_ms: 获取延迟.
        """
        game_time = 0.0
        game_data = data.get("gameData", {})
        if game_data:
            game_time = game_data.get("gameTime", 0.0)

        self._frames[source] = SourceFrame(
            source=source,
            timestamp=time.monotonic(),
            game_time=game_time,
            data=data,
            latency_ms=latency_ms,
        )

    def fuse(self) -> FusedSnapshot:
        """执行一次融合, 生成快照.

        Returns:
            融合后的 FusedSnapshot.
        """
        self._fuse_count += 1

        # 过滤过期数据
        fresh_frames = self._get_fresh_frames()

        if not fresh_frames:
            # 无新鲜数据, 返回降级快照
            return FusedSnapshot(
                quality=0.0,
                supplementary={"reason": "no_fresh_data"},
            )

        # 主数据源选择
        primary = ConflictStrategy.by_priority(
            list(fresh_frames.values()),
            self._source_priority,
        )

        # 从主数据源构建基础快照
        snapshot = self._build_from_primary(primary)

        # 从辅助数据源补充信息
        for source, frame in fresh_frames.items():
            if source == primary.source:
                continue
            self._merge_supplementary(snapshot, frame)

        # 事件去重
        snapshot.events = self._dedup_events(snapshot.events)

        # 计算数据质量
        snapshot.quality = self._compute_quality(fresh_frames)

        self._last_snapshot = snapshot
        return snapshot

    def _get_fresh_frames(self) -> Dict[str, SourceFrame]:
        """获取新鲜的数据帧."""
        result: Dict[str, SourceFrame] = {}
        for source, frame in self._frames.items():
            if frame.age_ms <= self._freshness_threshold_ms:
                result[source] = frame
        return result

    def _build_from_primary(self, primary: SourceFrame) -> FusedSnapshot:
        """从主数据源构建基础快照."""
        data = primary.data

        # 提取各部分
        game_data = data.get("gameData", {})
        active_player = data.get("activePlayer", {})
        all_players = data.get("allPlayers", [])

        events_wrapper = data.get("events", {})
        events = events_wrapper.get("Events", []) if isinstance(events_wrapper, dict) else []

        return FusedSnapshot(
            game_time=game_data.get("gameTime", 0.0),
            sources_used=[primary.source],
            game_data=copy.deepcopy(game_data),
            active_player=copy.deepcopy(active_player),
            all_players=copy.deepcopy(all_players),
            events=copy.deepcopy(events),
        )

    def _merge_supplementary(
        self,
        snapshot: FusedSnapshot,
        frame: SourceFrame,
    ) -> None:
        """合并辅助数据源的补充信息."""
        snapshot.sources_used.append(frame.source)

        if frame.source == "fiddler":
            # Fiddler 可能提供额外的网络事件
            fiddler_events = frame.data.get("network_events", [])
            if fiddler_events:
                snapshot.supplementary["fiddler_events"] = fiddler_events

            # Fiddler 可能提供更精确的时间戳
            fiddler_timestamps = frame.data.get("timestamps", {})
            if fiddler_timestamps:
                snapshot.supplementary["precise_timestamps"] = fiddler_timestamps

        elif frame.source == "websocket":
            # WebSocket 提供实时事件
            ws_events = frame.data.get("ws_events", [])
            if ws_events:
                snapshot.supplementary["ws_events"] = ws_events

    def _dedup_events(
        self, events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """事件去重 (基于 EventID)."""
        deduped: List[Dict[str, Any]] = []
        for event in events:
            event_id = event.get("EventID")
            if event_id is not None:
                if event_id in self._seen_event_ids:
                    continue
                self._seen_event_ids.add(event_id)
            deduped.append(event)
        return deduped

    def _compute_quality(
        self, frames: Dict[str, SourceFrame],
    ) -> float:
        """计算数据质量分数.

        因素:
        - 有多少数据源在线 (权重 0.3)
        - 主数据源延迟 (权重 0.4)
        - 数据新鲜度 (权重 0.3)
        """
        # 数据源数量
        source_score = min(1.0, len(frames) / len(self._source_priority))

        # 主数据源延迟
        primary_frame = ConflictStrategy.by_priority(
            list(frames.values()), self._source_priority,
        )
        latency_score = 1.0
        if primary_frame.latency_ms > 500:
            latency_score = 0.3
        elif primary_frame.latency_ms > 200:
            latency_score = 0.6
        elif primary_frame.latency_ms > 100:
            latency_score = 0.8

        # 新鲜度
        freshness_score = 1.0
        if primary_frame.age_ms > 1000:
            freshness_score = 0.5
        elif primary_frame.age_ms > 500:
            freshness_score = 0.7

        quality = (
            source_score * 0.3
            + latency_score * 0.4
            + freshness_score * 0.3
        )
        return quality

    def reset(self) -> None:
        """重置 (新游戏开始时调用)."""
        self._frames.clear()
        self._seen_event_ids.clear()
        self._last_snapshot = None

    def stats(self) -> Dict[str, Any]:
        return {
            "fuse_count": self._fuse_count,
            "active_sources": list(self._frames.keys()),
            "seen_events": len(self._seen_event_ids),
            "last_quality": (
                round(self._last_snapshot.quality, 3)
                if self._last_snapshot else 0
            ),
            "source_ages_ms": {
                src: round(f.age_ms, 1)
                for src, f in self._frames.items()
            },
        }
