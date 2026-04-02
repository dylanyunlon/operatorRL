"""
ReplayMessages — Serialization protocol for recording and replay.
===================================================================
lolbot-HyperAI · Common

Defines ReplayFrame, ReplayMetadata, and ReplayIndex for the JSONL
recording/replay system used by Transport and ReplaySimulator.

Architecture position:
    modules/common/adapters/replay_messages.py   ← YOU ARE HERE
    ├─ Used by: canbus/transport.py (MessageRecorder)
    ├─ Used by: scripts/replay_simulator.py (MessageReplayer)
    └─ Stored as: JSONL files in logs/ or data/ directories
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ReplayFrame:
    """A single recorded message frame."""
    frame_id: int = 0
    channel: str = ""
    timestamp: float = 0.0
    game_time: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)
    source_module: str = ""
    schema_version: int = 1

    def to_jsonl(self) -> str:
        return json.dumps({
            "fid": self.frame_id,
            "ch": self.channel,
            "ts": round(self.timestamp, 4),
            "gt": round(self.game_time, 2),
            "src": self.source_module,
            "v": self.schema_version,
            "p": self.payload,
        }, separators=(",", ":"))

    @staticmethod
    def from_jsonl(line: str) -> "ReplayFrame":
        d = json.loads(line)
        return ReplayFrame(
            frame_id=d.get("fid", 0),
            channel=d.get("ch", ""),
            timestamp=d.get("ts", 0.0),
            game_time=d.get("gt", 0.0),
            source_module=d.get("src", ""),
            schema_version=d.get("v", 1),
            payload=d.get("p", {}),
        )


@dataclass
class ReplayMetadata:
    """Metadata header for a replay recording."""
    session_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    game_duration_s: float = 0.0
    total_frames: int = 0
    channels_recorded: List[str] = field(default_factory=list)
    lolbot_version: str = "0.1.0"
    recorded_at: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "type": "metadata",
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "game_duration_s": round(self.game_duration_s, 1),
            "total_frames": self.total_frames,
            "channels": self.channels_recorded,
            "version": self.lolbot_version,
            "recorded_at": self.recorded_at,
        }, indent=2)

    @staticmethod
    def from_json(text: str) -> "ReplayMetadata":
        d = json.loads(text)
        return ReplayMetadata(
            session_id=d.get("session_id", ""),
            start_time=d.get("start_time", 0.0),
            end_time=d.get("end_time", 0.0),
            game_duration_s=d.get("game_duration_s", 0.0),
            total_frames=d.get("total_frames", 0),
            channels_recorded=d.get("channels", []),
            lolbot_version=d.get("version", "0.1.0"),
            recorded_at=d.get("recorded_at", ""),
        )


@dataclass
class ReplayIndex:
    """Time-based index for fast seeking into replay files.

    Stores (game_time, file_offset) pairs at regular intervals.
    """
    entries: List[Dict[str, Any]] = field(default_factory=list)
    interval_s: float = 10.0

    def add_entry(self, game_time: float, offset: int, frame_id: int) -> None:
        self.entries.append({
            "gt": round(game_time, 1),
            "off": offset,
            "fid": frame_id,
        })

    def seek_to_time(self, target_time: float) -> Optional[int]:
        """Find the file offset closest to target_time."""
        if not self.entries:
            return None
        best = self.entries[0]
        for entry in self.entries:
            if entry["gt"] <= target_time:
                best = entry
            else:
                break
        return best["off"]

    def to_json(self) -> str:
        return json.dumps({"interval_s": self.interval_s, "entries": self.entries})

    @staticmethod
    def from_json(text: str) -> "ReplayIndex":
        d = json.loads(text)
        idx = ReplayIndex(interval_s=d.get("interval_s", 10.0))
        idx.entries = d.get("entries", [])
        return idx
