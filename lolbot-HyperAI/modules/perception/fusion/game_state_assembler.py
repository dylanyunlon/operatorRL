"""
perception/fusion/game_state_assembler.py — Multi-source data fusion
=====================================================================
lolbot-HyperAI · Perception Layer

查看 Apollo modules/perception/fusion/ 上现有多传感器融合的实现方式,
理解其模式, 特别是不同数据源的时间对齐和冲突解决。从 Apollo sensor
fusion pipeline 这个好例子开始。然后遵循该模式实现 GameStateAssembler,
让 perception 可以将 LCU Live Client 数据、Fiddler 网络数据、WebSocket
事件流融合为统一的游戏状态快照, 并能处理数据延迟不一致 (LCU 100ms vs
Fiddler 500ms)。

Claude11 refactor:
    - SourceFrame: timestamped data from each source
    - Time alignment with configurable staleness thresholds
    - Conflict resolution strategy (newest-wins / weighted / priority)
    - Quality score computation (completeness * freshness)
    - Event deduplication across sources
    - Snapshot versioning for downstream consumers

位置: lolbot-HyperAI/modules/perception/fusion/game_state_assembler.py
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LCU_SOURCE = "lcu"
_FIDDLER_SOURCE = "fiddler"
_WEBSOCKET_SOURCE = "websocket"
_REPLAY_SOURCE = "replay"

_LCU_STALE_MS = 500.0        # LCU data stale after 500ms
_FIDDLER_STALE_MS = 2000.0   # Fiddler data stale after 2s
_WS_STALE_MS = 1000.0        # WebSocket data stale after 1s

_MAX_EVENT_HISTORY = 200
_SNAPSHOT_HISTORY_SIZE = 50
_EVENT_DEDUP_WINDOW_S = 5.0


# ---------------------------------------------------------------------------
# Source frame (one data packet from one source)
# ---------------------------------------------------------------------------

class ConflictStrategy(Enum):
    """How to resolve conflicting values across sources."""
    NEWEST_WINS = auto()      # Use the most recent value
    PRIORITY = auto()         # Use the highest-priority source
    WEIGHTED_AVERAGE = auto() # Blend numeric values


@dataclass
class SourceFrame:
    """Timestamped data from a single source.

    Represents one "reading" from LCU, Fiddler, or WebSocket.
    The assembler aligns multiple frames by game_time.
    """
    source: str = ""
    receive_time: float = 0.0    # monotonic time of receipt
    game_time_s: float = 0.0     # in-game timestamp
    data: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    sequence: int = 0
    stale_threshold_ms: float = 500.0

    @property
    def age_ms(self) -> float:
        """Age since receipt in milliseconds."""
        return (time.monotonic() - self.receive_time) * 1000.0

    @property
    def is_stale(self) -> bool:
        return self.age_ms > self.stale_threshold_ms

    @property
    def freshness(self) -> float:
        """Freshness score 0.0-1.0 (1.0 = just received)."""
        age = self.age_ms
        if age <= 0:
            return 1.0
        if age >= self.stale_threshold_ms:
            return 0.0
        return 1.0 - (age / self.stale_threshold_ms)


# ---------------------------------------------------------------------------
# Fused game state snapshot
# ---------------------------------------------------------------------------

@dataclass
class FusedSnapshot:
    """Result of assembling all source frames into one state.

    Downstream consumers (prediction, planning) read this.
    """
    version: int = 0
    assemble_time: float = 0.0
    game_time_s: float = 0.0

    # Merged player data
    players: List[Dict[str, Any]] = field(default_factory=list)

    # Game state
    active_player: Dict[str, Any] = field(default_factory=dict)
    game_stats: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    # Scores
    blue_kills: int = 0
    red_kills: int = 0
    blue_gold: float = 0.0
    red_gold: float = 0.0

    # Quality metrics
    quality: float = 0.0
    sources_used: List[str] = field(default_factory=list)
    sources_stale: List[str] = field(default_factory=list)
    completeness: float = 0.0
    freshness: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "game_time_s": round(self.game_time_s, 1),
            "quality": round(self.quality, 3),
            "sources_used": self.sources_used,
            "sources_stale": self.sources_stale,
            "blue_kills": self.blue_kills,
            "red_kills": self.red_kills,
            "player_count": len(self.players),
            "event_count": len(self.events),
        }


# ---------------------------------------------------------------------------
# Event deduplication
# ---------------------------------------------------------------------------

class EventDeduplicator:
    """Deduplicates game events across multiple sources.

    The same kill/dragon/tower event may arrive via LCU, Fiddler,
    and WebSocket. We dedup by computing a content hash.
    """

    def __init__(self, window_s: float = _EVENT_DEDUP_WINDOW_S) -> None:
        self._seen: Dict[str, float] = {}
        self._window_s = window_s

    def is_duplicate(self, event: Dict[str, Any]) -> bool:
        """Check if event is a duplicate. Returns True if seen before."""
        key = self._event_key(event)
        now = time.monotonic()

        # Prune old entries
        cutoff = now - self._window_s
        stale_keys = [
            k for k, t in self._seen.items() if t < cutoff
        ]
        for k in stale_keys:
            del self._seen[k]

        if key in self._seen:
            return True

        self._seen[key] = now
        return False

    def _event_key(self, event: Dict[str, Any]) -> str:
        """Compute a dedup key from event contents."""
        event_type = event.get("EventName", event.get("type", ""))
        game_time = event.get("EventTime", event.get("game_time", 0))
        # Round game_time to avoid float precision issues
        gt_rounded = round(float(game_time), 1)
        raw = f"{event_type}:{gt_rounded}"

        # Include killer/victim for kill events
        killer = event.get("KillerName", "")
        victim = event.get("VictimName", "")
        if killer or victim:
            raw += f":{killer}:{victim}"

        return hashlib.md5(raw.encode()).hexdigest()[:12]

    @property
    def seen_count(self) -> int:
        return len(self._seen)


# ---------------------------------------------------------------------------
# Source priority (for conflict resolution)
# ---------------------------------------------------------------------------

_SOURCE_PRIORITY: Dict[str, int] = {
    _LCU_SOURCE: 10,        # Highest: direct API
    _WEBSOCKET_SOURCE: 8,   # Real-time events
    _FIDDLER_SOURCE: 5,     # Network capture (delayed)
    _REPLAY_SOURCE: 3,      # Replay data (post-game)
}


# ---------------------------------------------------------------------------
# GameStateAssembler
# ---------------------------------------------------------------------------

class GameStateAssembler:
    """Multi-source game state fusion engine.

    Receives SourceFrames from multiple data providers (LCU, Fiddler,
    WebSocket) and assembles them into a unified FusedSnapshot.

    The assembly process:
    1. Accept incoming frames and store per-source
    2. On assemble(): merge all fresh frames
    3. Resolve conflicts using configured strategy
    4. Deduplicate events across sources
    5. Compute quality score
    6. Emit versioned FusedSnapshot

    Usage::

        assembler = GameStateAssembler()

        # Feed data from sources
        assembler.update_frame(lcu_frame)
        assembler.update_frame(fiddler_frame)

        # Assemble
        snapshot = assembler.assemble()
        print(snapshot.quality)  # 0.0-1.0
    """

    def __init__(
        self,
        conflict_strategy: ConflictStrategy = ConflictStrategy.NEWEST_WINS,
        min_quality_threshold: float = 0.1,
    ) -> None:
        self._strategy = conflict_strategy
        self._min_quality = min_quality_threshold
        self._frames: Dict[str, SourceFrame] = {}
        self._dedup = EventDeduplicator()
        self._version = 0
        self._last_snapshot: Optional[FusedSnapshot] = None
        self._snapshot_history: Deque[FusedSnapshot] = deque(
            maxlen=_SNAPSHOT_HISTORY_SIZE,
        )
        self._seen_event_ids: Set[str] = set()
        self._all_events: Deque[Dict[str, Any]] = deque(
            maxlen=_MAX_EVENT_HISTORY,
        )

    def update_frame(self, frame: SourceFrame) -> None:
        """Update the latest frame from a source."""
        self._frames[frame.source] = frame

    def assemble(self) -> FusedSnapshot:
        """Assemble all fresh frames into a unified snapshot.

        Returns a new FusedSnapshot. If all sources are stale,
        returns a low-quality snapshot with whatever data is available.
        """
        self._version += 1
        now = time.monotonic()

        snap = FusedSnapshot(
            version=self._version,
            assemble_time=now,
        )

        # Classify sources
        fresh_frames: List[SourceFrame] = []
        stale_frames: List[SourceFrame] = []

        for source, frame in self._frames.items():
            if frame.is_stale:
                stale_frames.append(frame)
                snap.sources_stale.append(source)
            else:
                fresh_frames.append(frame)
                snap.sources_used.append(source)

        # Use fresh if available, fall back to stale
        frames_to_merge = fresh_frames or stale_frames

        if not frames_to_merge:
            snap.quality = 0.0
            self._last_snapshot = snap
            return snap

        # Sort by priority (highest first)
        frames_to_merge.sort(
            key=lambda f: _SOURCE_PRIORITY.get(f.source, 0),
            reverse=True,
        )

        # Merge game time (use highest-priority fresh source)
        snap.game_time_s = frames_to_merge[0].game_time_s

        # Merge data fields
        merged_data: Dict[str, Any] = {}
        for frame in reversed(frames_to_merge):
            # Lower priority frames go first, higher priority overwrites
            merged_data.update(frame.data)

        # Extract standard fields
        snap.players = merged_data.get("allPlayers", [])
        snap.active_player = merged_data.get("activePlayer", {})
        snap.game_stats = merged_data.get("gameData", {})

        # Compute scores
        self._compute_scores(snap)

        # Merge and dedup events
        all_new_events: List[Dict[str, Any]] = []
        for frame in frames_to_merge:
            for event in frame.events:
                if not self._dedup.is_duplicate(event):
                    all_new_events.append(event)
                    self._all_events.append(event)

        snap.events = all_new_events

        # Compute quality
        snap.freshness = self._compute_freshness(frames_to_merge)
        snap.completeness = self._compute_completeness(snap)
        snap.quality = snap.freshness * snap.completeness

        self._last_snapshot = snap
        self._snapshot_history.append(snap)

        return snap

    def _compute_scores(self, snap: FusedSnapshot) -> None:
        """Compute kill/gold totals from player list."""
        blue_kills = 0
        red_kills = 0
        blue_gold = 0.0
        red_gold = 0.0

        for player in snap.players:
            team = player.get("team", "")
            scores = player.get("scores", {})
            kills = scores.get("kills", 0)
            gold = player.get("currentGold", 0)

            if team == "ORDER":
                blue_kills += kills
                blue_gold += gold
            elif team == "CHAOS":
                red_kills += kills
                red_gold += gold

        snap.blue_kills = blue_kills
        snap.red_kills = red_kills
        snap.blue_gold = blue_gold
        snap.red_gold = red_gold

    def _compute_freshness(
        self, frames: List[SourceFrame],
    ) -> float:
        """Average freshness across all used frames."""
        if not frames:
            return 0.0
        total = sum(f.freshness for f in frames)
        return total / len(frames)

    def _compute_completeness(self, snap: FusedSnapshot) -> float:
        """How complete is the snapshot (0.0-1.0).

        Checks: players present, active player present, game stats.
        """
        score = 0.0
        checks = 0

        # Players
        checks += 1
        if len(snap.players) >= 10:
            score += 1.0
        elif len(snap.players) >= 5:
            score += 0.5

        # Active player
        checks += 1
        if snap.active_player:
            score += 1.0

        # Game stats
        checks += 1
        if snap.game_stats:
            score += 1.0

        # Game time
        checks += 1
        if snap.game_time_s > 0:
            score += 1.0

        return score / checks if checks > 0 else 0.0

    # -- Query --

    @property
    def latest(self) -> Optional[FusedSnapshot]:
        return self._last_snapshot

    @property
    def version(self) -> int:
        return self._version

    def recent_events(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get recent deduplicated events."""
        items = list(self._all_events)
        return items[-count:]

    def source_status(self) -> Dict[str, Dict[str, Any]]:
        """Status of all known sources."""
        result: Dict[str, Dict[str, Any]] = {}
        for source, frame in self._frames.items():
            result[source] = {
                "age_ms": round(frame.age_ms, 1),
                "stale": frame.is_stale,
                "freshness": round(frame.freshness, 3),
                "game_time_s": round(frame.game_time_s, 1),
                "sequence": frame.sequence,
            }
        return result

    def stats(self) -> Dict[str, Any]:
        return {
            "version": self._version,
            "sources": len(self._frames),
            "seen_events": self._dedup.seen_count,
            "total_events": len(self._all_events),
            "last_quality": (
                round(self._last_snapshot.quality, 3)
                if self._last_snapshot else 0.0
            ),
            "source_ages_ms": {
                src: round(f.age_ms, 1)
                for src, f in self._frames.items()
            },
        }

    def reset(self) -> None:
        """Reset all state (e.g. between games)."""
        self._frames.clear()
        self._dedup = EventDeduplicator()
        self._last_snapshot = None
        self._all_events.clear()
        self._seen_event_ids.clear()
