"""
Replay Recorder - Records game sessions for offline replay and analysis.

Stores timestamped game snapshots and advice events, enabling
post-game review and offline strategy backtesting.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from lol_fiddler_agent.agents.strategy_agent import StrategicAdvice
from lol_fiddler_agent.models.game_snapshot import GameSnapshot

logger = logging.getLogger(__name__)


@dataclass
class ReplayEvent:
    """A single event in a replay timeline."""
    timestamp: float  # game time in seconds
    event_type: str  # "snapshot", "advice", "feedback", "lifecycle"
    data: dict[str, Any] = field(default_factory=dict)
    wall_time: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.timestamp,
            "type": self.event_type,
            "data": self.data,
            "wall": self.wall_time,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReplayEvent":
        return cls(
            timestamp=d["ts"],
            event_type=d["type"],
            data=d.get("data", {}),
            wall_time=d.get("wall", 0),
        )


@dataclass
class ReplayMetadata:
    """Metadata for a recorded replay."""
    replay_id: str = ""
    champion: str = ""
    game_mode: str = "CLASSIC"
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0
    event_count: int = 0
    snapshot_count: int = 0
    won: Optional[bool] = None
    patch_version: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "champion": self.champion,
            "game_mode": self.game_mode,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "event_count": self.event_count,
            "snapshot_count": self.snapshot_count,
            "won": self.won,
            "patch_version": self.patch_version,
            "notes": self.notes,
        }


class ReplayRecorder:
    """Records game session events for offline replay.

    Example::

        recorder = ReplayRecorder("./replays")
        recorder.start_recording("game_123")
        recorder.record_snapshot(snapshot)
        recorder.record_advice(advice)
        filepath = recorder.stop_recording()
    """

    def __init__(self, replay_dir: str = "./replays") -> None:
        self._replay_dir = Path(replay_dir)
        self._replay_dir.mkdir(parents=True, exist_ok=True)
        self._events: list[ReplayEvent] = []
        self._metadata = ReplayMetadata()
        self._recording = False
        self._snapshot_count = 0

    def start_recording(self, replay_id: str = "") -> None:
        if self._recording:
            logger.warning("Already recording, stopping previous")
            self.stop_recording()
        self._events.clear()
        self._snapshot_count = 0
        self._metadata = ReplayMetadata(
            replay_id=replay_id or f"replay_{int(time.time())}",
            start_time=time.time(),
        )
        self._recording = True
        logger.info("Started recording: %s", self._metadata.replay_id)

    def record_snapshot(self, snapshot: GameSnapshot) -> None:
        if not self._recording:
            return
        event = ReplayEvent(
            timestamp=snapshot.game_time,
            event_type="snapshot",
            data=json.loads(snapshot.to_json()),
        )
        self._events.append(event)
        self._snapshot_count += 1

        # Update metadata
        if self._metadata.champion == "":
            self._metadata.champion = snapshot.my_champion
        self._metadata.game_mode = snapshot.game_mode

    def record_advice(self, advice: StrategicAdvice, game_time: float = 0.0) -> None:
        if not self._recording:
            return
        event = ReplayEvent(
            timestamp=game_time,
            event_type="advice",
            data=advice.model_dump(),
        )
        self._events.append(event)

    def record_event(self, event_type: str, data: dict[str, Any], game_time: float = 0.0) -> None:
        if not self._recording:
            return
        self._events.append(ReplayEvent(
            timestamp=game_time,
            event_type=event_type,
            data=data,
        ))

    def stop_recording(self, won: Optional[bool] = None) -> str:
        """Stop recording and save to disk. Returns filepath."""
        if not self._recording:
            return ""

        self._recording = False
        self._metadata.end_time = time.time()
        self._metadata.duration_seconds = self._metadata.end_time - self._metadata.start_time
        self._metadata.event_count = len(self._events)
        self._metadata.snapshot_count = self._snapshot_count
        self._metadata.won = won

        filepath = self._save()
        logger.info(
            "Stopped recording: %s (%d events, %.0fs)",
            self._metadata.replay_id,
            len(self._events),
            self._metadata.duration_seconds,
        )
        return filepath

    def _save(self) -> str:
        """Save replay to compressed JSON file."""
        filename = f"{self._metadata.replay_id}.replay.jsonl.gz"
        filepath = self._replay_dir / filename

        with gzip.open(filepath, "wt", encoding="utf-8") as f:
            # First line: metadata
            f.write(json.dumps({"_meta": self._metadata.to_dict()}) + "\n")
            # Remaining lines: events
            for event in self._events:
                f.write(json.dumps(event.to_dict(), default=str) + "\n")

        return str(filepath)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def metadata(self) -> ReplayMetadata:
        return self._metadata


class ReplayPlayer:
    """Plays back recorded game sessions.

    Example::

        player = ReplayPlayer()
        player.load("./replays/game_123.replay.jsonl.gz")
        for event in player.events():
            if event.event_type == "snapshot":
                render(event.data)
    """

    def __init__(self) -> None:
        self._metadata: Optional[ReplayMetadata] = None
        self._events: list[ReplayEvent] = []
        self._loaded = False

    def load(self, filepath: str) -> bool:
        """Load a replay file."""
        try:
            self._events.clear()
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    data = json.loads(line)
                    if i == 0 and "_meta" in data:
                        meta = data["_meta"]
                        self._metadata = ReplayMetadata(**meta)
                    else:
                        self._events.append(ReplayEvent.from_dict(data))
            self._loaded = True
            logger.info("Loaded replay: %d events", len(self._events))
            return True
        except Exception as e:
            logger.error("Failed to load replay: %s", e)
            return False

    def events(self, event_type: Optional[str] = None) -> Iterator[ReplayEvent]:
        """Iterate over replay events, optionally filtered by type."""
        for event in self._events:
            if event_type is None or event.event_type == event_type:
                yield event

    def snapshots(self) -> Iterator[ReplayEvent]:
        return self.events("snapshot")

    def advice_events(self) -> Iterator[ReplayEvent]:
        return self.events("advice")

    def get_snapshot_at(self, game_time: float) -> Optional[ReplayEvent]:
        """Get the snapshot closest to a given game time."""
        closest: Optional[ReplayEvent] = None
        min_diff = float("inf")
        for event in self.events("snapshot"):
            diff = abs(event.timestamp - game_time)
            if diff < min_diff:
                min_diff = diff
                closest = event
        return closest

    @property
    def metadata(self) -> Optional[ReplayMetadata]:
        return self._metadata

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def total_events(self) -> int:
        return len(self._events)

    @property
    def duration(self) -> float:
        if not self._events:
            return 0.0
        return self._events[-1].timestamp - self._events[0].timestamp


def list_replays(replay_dir: str = "./replays") -> list[dict[str, Any]]:
    """List available replay files with metadata."""
    replays: list[dict[str, Any]] = []
    replay_path = Path(replay_dir)
    if not replay_path.exists():
        return replays
    for filepath in sorted(replay_path.glob("*.replay.jsonl.gz")):
        try:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                first_line = f.readline()
                data = json.loads(first_line)
                if "_meta" in data:
                    meta = data["_meta"]
                    meta["filepath"] = str(filepath)
                    replays.append(meta)
        except Exception as e:
            logger.debug("Could not read replay %s: %s", filepath, e)
    return replays
