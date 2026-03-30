"""
Fiddler Replay Recorder — complete match protocol stream recording.

Records every Fiddler-captured frame during a live game session into
a replayable sequence, with metadata, optional compression, and
thread-safe concurrent writes.

Location: extensions/fiddler_bridge/src/fiddler_replay_recorder.py

Reference (拿来主义):
  - Akagi/mitm/bridge/majsoul/liqi.py: sequential frame logging
  - DI-star replay parsing: frame-by-frame replay structure
  - extensions/fiddler-bridge/src/fiddler_replay_engine.py: existing replay stub
  - extensions/fiddler-bridge/src/fiddler_session_manager.py: session lifecycle
  - integrations/lol/src/lol_agent/training_data_sqlite.py: persistence pattern

Design Notes (Knuth-level critique):
  User:
    - Recording ignores frames when not in recording state — no silent data loss.
    - max_frames prevents runaway memory in ultra-long sessions.
    - export_replay() always succeeds even if no frames recorded.
  System:
    - Lock per-frame-list, not global — concurrent writers don't block readers.
    - Compression is optional and lazy — only applied at export time.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_replay_recorder.v1"
_DEFAULT_MAX_FRAMES: int = 100_000


class ReplayMetadata:
    """Metadata for a recorded replay session.

    Captures match_id, start/stop wall-clock times, first/last game_time,
    and frame count for the replay header.
    """

    __slots__ = (
        "match_id",
        "start_time",
        "stop_time",
        "first_game_time",
        "last_game_time",
        "frame_count",
    )

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self.start_time: float = time.time()
        self.stop_time: float = 0.0
        self.first_game_time: Optional[float] = None
        self.last_game_time: Optional[float] = None
        self.frame_count: int = 0

    def update(self, frame: Dict[str, Any]) -> None:
        gt = frame.get("game_time", None)
        if gt is not None and isinstance(gt, (int, float)):
            if self.first_game_time is None:
                self.first_game_time = float(gt)
            self.last_game_time = float(gt)
        self.frame_count += 1

    def finalize(self) -> None:
        self.stop_time = time.time()

    @property
    def duration(self) -> float:
        if self.first_game_time is not None and self.last_game_time is not None:
            return self.last_game_time - self.first_game_time
        return 0.0

    @property
    def wall_duration(self) -> float:
        if self.stop_time > 0:
            return self.stop_time - self.start_time
        return time.time() - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "start_time": self.start_time,
            "stop_time": self.stop_time,
            "first_game_time": self.first_game_time,
            "last_game_time": self.last_game_time,
            "duration": self.duration,
            "wall_duration": self.wall_duration,
            "frame_count": self.frame_count,
        }


class FiddlerReplayRecorder:
    """Record complete match protocol streams for later replay.

    Lifecycle:
        1. ``start_recording(match_id)`` — begin a new recording session.
        2. ``record_frame(frame_dict)`` — append a frame (thread-safe).
        3. ``stop_recording()`` — finalise metadata.
        4. ``export_replay()`` — get the full replay dict.

    Attributes:
        is_recording: Whether a recording session is active.
        frame_count: Number of frames recorded in current session.
        evolution_callback: Optional callback for self-evolution events.

    Reference (拿来主义):
        - Akagi liqi.py: sequential log of protobuf messages
        - DI-star replay: frame sequence with metadata header
    """

    def __init__(
        self,
        *,
        max_frames: int = _DEFAULT_MAX_FRAMES,
        compress: bool = False,
    ) -> None:
        self._max_frames = max_frames
        self._compress = compress

        self._recording = False
        self._metadata: Optional[ReplayMetadata] = None
        self._frames: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    @property
    def max_frames(self) -> int:
        return self._max_frames

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_recording(self, match_id: str) -> None:
        """Start a new recording session.

        Clears any previous frames and initialises fresh metadata.
        """
        with self._lock:
            self._frames = []
            self._metadata = ReplayMetadata(match_id)
            self._recording = True
        logger.info("ReplayRecorder: started recording match=%s", match_id)
        self._fire_evolution({"action": "start_recording", "match_id": match_id})

    def stop_recording(self) -> None:
        """Finalise the current recording session."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            if self._metadata:
                self._metadata.finalize()
        logger.info("ReplayRecorder: stopped recording — %d frames", self.frame_count)
        self._fire_evolution({"action": "stop_recording", "frame_count": self.frame_count})

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_frame(self, frame: Dict[str, Any]) -> bool:
        """Append a frame to the current recording.

        Returns True if the frame was accepted; False if not recording
        or max_frames reached.  Thread-safe.
        """
        with self._lock:
            if not self._recording:
                return False
            if len(self._frames) >= self._max_frames:
                return False

            stamped = dict(frame)
            stamped.setdefault("record_ts", time.time())
            self._frames.append(stamped)

            if self._metadata:
                self._metadata.update(stamped)

        self._fire_evolution({"action": "record_frame"})
        return True

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_replay(self) -> Dict[str, Any]:
        """Export the full replay as a dict.

        Contains metadata header + ordered frame list.  If compression
        was enabled, the frames value is a gzip-compressed JSON bytes
        string and ``compressed`` is True.
        """
        with self._lock:
            meta = self._metadata.to_dict() if self._metadata else {"match_id": "unknown"}
            frames_copy = list(self._frames)

        replay: Dict[str, Any] = {
            **meta,
            "recorder_version": _EVOLUTION_KEY,
        }

        if self._compress and frames_copy:
            raw_json = json.dumps(frames_copy, ensure_ascii=False, default=str)
            replay["frames_gz"] = gzip.compress(raw_json.encode("utf-8"))
            replay["compressed"] = True
            replay["frames"] = frames_copy  # also keep decoded for convenience
        else:
            replay["frames"] = frames_copy
            replay["compressed"] = False

        return replay

    def export_json(self) -> str:
        """Export replay as JSON string (uncompressed)."""
        replay = self.export_replay()
        replay.pop("frames_gz", None)
        return json.dumps(replay, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # Import (for test / replay verification)
    # ------------------------------------------------------------------

    def import_replay(self, data: Dict[str, Any]) -> None:
        """Import a previously exported replay for verification."""
        with self._lock:
            self._frames = data.get("frames", [])
            mid = data.get("match_id", "imported")
            self._metadata = ReplayMetadata(mid)
            self._metadata.frame_count = len(self._frames)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            fc = len(self._frames)
        return {
            "is_recording": self._recording,
            "frame_count": fc,
            "max_frames": self._max_frames,
            "compress": self._compress,
            "metadata": self._metadata.to_dict() if self._metadata else None,
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised in FiddlerReplayRecorder")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"FiddlerReplayRecorder(recording={self._recording}, "
            f"frames={self.frame_count}/{self._max_frames})"
        )


# ---------------------------------------------------------------------------
# Module-level convenience singleton
# ---------------------------------------------------------------------------
default_recorder: FiddlerReplayRecorder = FiddlerReplayRecorder()
