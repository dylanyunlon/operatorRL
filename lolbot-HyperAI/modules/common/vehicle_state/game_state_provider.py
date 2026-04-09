"""
GameStateProvider — Apollo VehicleStateProvider equivalent.
=============================================================
lolbot-HyperAI · modules/common/vehicle_state

查看 Apollo ``modules/common/vehicle_state/vehicle_state_provider.cc`` 上现有
``VehicleStateProvider`` 的实现方式, 理解其模式, 特别是 singleton + Update()
+ 多线程读者安全的设计。从 Apollo VehicleStateProvider 这个好例子开始。
然后, 遵循该模式实现一个新的 ``GameStateProvider``, 让所有组件可以安全
读取最新 GameSnapshot, 并能避免重复解析和竞态条件。接着引入 sequence_num
单调递增, 使消费者能够检测是否已处理过同一快照, 同时优化读锁为 RLock
(允许同线程重入)。随后整合 ``_last_update_time`` 时间戳, 令 stale 检测
支持 wall-clock 判定, 进而增强数据新鲜度保障。最终完善 ``status()`` 导出,
确保兼容 Dreamview 仪表盘, 全面升级状态提供质量以达成 Apollo 数据一致性。

位置: lolbot-HyperAI/modules/common/vehicle_state/game_state_provider.py

Apollo reference:
    modules/common/vehicle_state/vehicle_state_provider.h
    modules/common/vehicle_state/vehicle_state_provider.cc
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GameStateSnapshot:
    """Lightweight game state snapshot — domain equivalent of VehicleState.

    Contains the derived, authoritative game state that components
    should read (not the raw LCU data, which is in canbus).
    """

    game_time: float = 0.0
    game_mode: str = ""
    map_name: str = ""

    # Player state
    player_name: str = ""
    player_champion: str = ""
    player_level: int = 0
    player_gold: float = 0.0
    player_cs: int = 0
    player_kills: int = 0
    player_deaths: int = 0
    player_assists: int = 0

    # Team state
    ally_gold: float = 0.0
    enemy_gold: float = 0.0
    gold_diff: float = 0.0
    ally_turrets: int = 0
    enemy_turrets: int = 0
    ally_dragons: int = 0
    enemy_dragons: int = 0

    # Metadata
    sequence_num: int = 0
    timestamp: float = 0.0
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time": self.game_time,
            "game_mode": self.game_mode,
            "player_name": self.player_name,
            "player_champion": self.player_champion,
            "gold_diff": self.gold_diff,
            "sequence_num": self.sequence_num,
            "timestamp": self.timestamp,
        }


class GameStateProvider:
    """Thread-safe singleton providing the latest game state.

    Apollo equivalent: ``VehicleStateProvider`` — a singleton that
    ``perception`` writes to and ``prediction/planning/control`` read
    from. Uses RLock for thread safety with reentrant reads.

    Usage::

        provider = GameStateProvider.instance()

        # Writer (perception component):
        provider.update(snapshot)

        # Reader (any component):
        state = provider.latest()
        if state and state.sequence_num > my_last_seq:
            # Process new state
            my_last_seq = state.sequence_num
    """

    _instance: Optional[GameStateProvider] = None
    _cls_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: Optional[GameStateSnapshot] = None
        self._sequence_num: int = 0
        self._update_count: int = 0
        self._last_update_time: float = 0.0
        self._stale_threshold_s: float = 5.0

    @classmethod
    def instance(cls) -> GameStateProvider:
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._cls_lock:
            cls._instance = None

    def update(self, snapshot: GameStateSnapshot) -> int:
        """Update the latest game state.

        Apollo equivalent: ``VehicleStateProvider::Update(localization)``

        Called by perception after assembling a GameSnapshot from raw
        canbus data. Returns the assigned sequence number.
        """
        with self._lock:
            self._sequence_num += 1
            snapshot.sequence_num = self._sequence_num
            snapshot.timestamp = time.time()
            self._current = snapshot
            self._update_count += 1
            self._last_update_time = time.monotonic()
            return self._sequence_num

    def latest(self) -> Optional[GameStateSnapshot]:
        """Get the latest game state (read-only).

        Thread-safe. Returns None if no state has been written yet.
        """
        with self._lock:
            return self._current

    def sequence_num(self) -> int:
        """Get current sequence number without copying the full state."""
        with self._lock:
            return self._sequence_num

    def is_stale(self) -> bool:
        """Check if the game state is stale (not updated recently).

        Apollo equivalent: checking timestamp diff in OnControlCommandCheck.
        """
        with self._lock:
            if self._last_update_time <= 0:
                return True
            age = time.monotonic() - self._last_update_time
            return age > self._stale_threshold_s

    def age_seconds(self) -> float:
        """Time since last update in seconds."""
        with self._lock:
            if self._last_update_time <= 0:
                return float("inf")
            return time.monotonic() - self._last_update_time

    def set_stale_threshold(self, seconds: float) -> None:
        """Configure the stale detection threshold."""
        with self._lock:
            self._stale_threshold_s = seconds

    def status(self) -> Dict[str, Any]:
        """Export provider status for Dreamview / monitoring."""
        with self._lock:
            return {
                "has_state": self._current is not None,
                "sequence_num": self._sequence_num,
                "update_count": self._update_count,
                "last_update_time": self._last_update_time,
                "age_s": round(self.age_seconds(), 3),
                "is_stale": self.is_stale(),
                "stale_threshold_s": self._stale_threshold_s,
                "game_time": (
                    self._current.game_time if self._current else 0.0
                ),
            }
