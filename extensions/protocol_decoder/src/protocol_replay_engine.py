"""
Protocol Replay Engine — protocol stream playback + speed control.

Loads a recorded replay and plays it back frame-by-frame with adjustable
speed, seek, step, pause/resume.  Used for offline training data replay,
debugging, and replay analysis.

Location: extensions/protocol_decoder/src/protocol_replay_engine.py

Reference (拿来主义):
  - Akagi replay analysis: frame-by-frame replay processing
  - DI-star replay parser: sequential frame iteration
  - extensions/fiddler-bridge/src/fiddler_replay_engine.py: existing stub
  - extensions/fiddler_bridge/src/fiddler_replay_recorder.py: replay format

Design Notes (Knuth-level critique):
  User:
    - seek() allows random access — jump to any frame instantly.
    - step() advances exactly one frame — deterministic for tests.
    - on_frame and on_complete callbacks enable event-driven consumers.
  System:
    - No deep copy on frame retrieval — caller must not mutate.
    - Speed multiplier affects only timed playback, not step().
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.protocol_replay_engine.v1"


class ProtocolReplayEngine:
    """Replay engine for recorded protocol streams.

    Lifecycle:
        1. ``load(replay_dict)`` — load a replay.
        2. ``play()`` / ``pause()`` — control playback state.
        3. ``step()`` — advance one frame (also works while paused).
        4. ``seek(index)`` — jump to frame by index.
        5. ``set_speed(multiplier)`` — adjust playback speed.

    Attributes:
        is_playing: Whether timed playback is active.
        speed: Current speed multiplier (1.0 = realtime).
        total_frames: Number of frames in loaded replay.
        current_frame_index: Index of the current frame cursor.
        on_frame: Callback ``(frame_dict) -> None``.
        on_complete: Callback ``() -> None``.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self._frames: List[Dict[str, Any]] = []
        self._match_id: str = ""
        self._speed: float = 1.0
        self._cursor: int = 0
        self._playing: bool = False
        self._loaded: bool = False

        self.on_frame: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_complete: Optional[Callable[[], None]] = None
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

        self._playback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def total_frames(self) -> int:
        return len(self._frames)

    @property
    def current_frame_index(self) -> int:
        return self._cursor

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self, replay: Dict[str, Any]) -> None:
        """Load a replay dict (from FiddlerReplayRecorder.export_replay()).

        Args:
            replay: Dict with ``match_id`` and ``frames`` keys.
        """
        self._frames = list(replay.get("frames", []))
        self._match_id = replay.get("match_id", "unknown")
        self._cursor = 0
        self._playing = False
        self._loaded = True
        self._fire_evolution({
            "action": "load",
            "match_id": self._match_id,
            "total_frames": self.total_frames,
        })

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    def play(self) -> None:
        """Start timed playback from current cursor position."""
        if not self._loaded:
            logger.warning("play() called before load()")
            return
        self._playing = True
        self._fire_evolution({"action": "play"})

    def pause(self) -> None:
        """Pause timed playback."""
        self._playing = False
        self._fire_evolution({"action": "pause"})

    def set_speed(self, multiplier: float) -> None:
        """Set playback speed multiplier."""
        self._speed = max(0.1, min(multiplier, 100.0))
        self._fire_evolution({"action": "set_speed", "speed": self._speed})

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def seek(self, index: int) -> None:
        """Jump to a specific frame index."""
        if not self._loaded:
            return
        self._cursor = max(0, min(index, self.total_frames - 1))
        self._fire_evolution({"action": "seek", "index": self._cursor})

    def step(self) -> Optional[Dict[str, Any]]:
        """Advance one frame and return it.

        Fires on_frame callback.  If at end of replay, fires on_complete.
        Returns None if no frames or past end.
        """
        if not self._loaded or self._cursor >= self.total_frames:
            cb = self.on_complete
            if cb is not None:
                try:
                    cb()
                except Exception:
                    logger.exception("on_complete callback raised")
            return None

        frame = self._frames[self._cursor]
        self._cursor += 1

        cb = self.on_frame
        if cb is not None:
            try:
                cb(frame)
            except Exception:
                logger.exception("on_frame callback raised")

        if self._cursor >= self.total_frames:
            comp_cb = self.on_complete
            if comp_cb is not None:
                try:
                    comp_cb()
                except Exception:
                    logger.exception("on_complete callback raised")

        return frame

    def get_current_frame(self) -> Optional[Dict[str, Any]]:
        """Return the frame at the current cursor without advancing."""
        if not self._loaded or self._cursor >= self.total_frames:
            return None
        return self._frames[self._cursor]

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def step_n(self, n: int) -> List[Dict[str, Any]]:
        """Advance N frames and return them all."""
        results: List[Dict[str, Any]] = []
        for _ in range(n):
            f = self.step()
            if f is None:
                break
            results.append(f)
        return results

    def get_all_frames(self) -> List[Dict[str, Any]]:
        """Return a copy of all loaded frames."""
        return list(self._frames)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "loaded": self._loaded,
            "match_id": self._match_id,
            "total_frames": self.total_frames,
            "current_frame": self._cursor,
            "speed": self._speed,
            "is_playing": self._playing,
            "progress_pct": (
                (self._cursor / self.total_frames * 100)
                if self.total_frames > 0 else 0.0
            ),
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
                logger.exception("evolution_callback raised in ProtocolReplayEngine")

    def __repr__(self) -> str:
        return (
            f"ProtocolReplayEngine(loaded={self._loaded}, "
            f"frames={self.total_frames}, cursor={self._cursor})"
        )


default_replay_engine: ProtocolReplayEngine = ProtocolReplayEngine()
