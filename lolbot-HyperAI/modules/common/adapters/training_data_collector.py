"""
TrainingDataCollector — Per-tick snapshot → training matrix pipeline.
======================================================================
lolbot-HyperAI · Common Adapters

Records every Proc() cycle's game state, features, and predictions as
training samples. On game end, exports to CSV/JSONL for offline model
training. This closes the reinforcement loop: play → collect → train →
deploy → play with better weights.

Architecture position:
    modules/common/adapters/training_data_collector.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (GameSnapshot)
    ├─ Reads: /lol/win_prediction (WinPrediction)
    ├─ Reads: /lol/teamfight_assessment (TeamfightAssessment)
    ├─ Output: data/*.jsonl, data/*.csv training files
    └─ Used by: launch/main_loop.py (post-game callback)

Apollo reference:
    cyber/record/record_writer.cc — rosbag-style recording
    modules/data/warehouse/ — offline data pipeline

Design notes:
    - Samples stored in memory during game, flushed on game_end
    - Each sample: timestamp, game_time, feature_vector, win_prob,
      teamfight_action, gold_diff, phase, actual_outcome (post-game)
    - JSONL format for streaming, CSV for pandas/sklearn
    - Configurable max_samples to cap memory usage
    - Thread-safe: called from main loop Proc() thread only
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_MAX_SAMPLES = 10000          # ~17 min at 10Hz, ~33 min at 5Hz
_FLUSH_BATCH = 500            # write to disk every N samples
_DEFAULT_DATA_DIR = "data/training"


@dataclass
class TrainingSample:
    """A single training data point from one Proc() tick."""
    timestamp: float
    game_time: float
    phase: str
    gold_diff: float
    kill_diff: int
    tower_diff: int
    dragon_diff: int
    blue_alive: int
    red_alive: int
    blue_avg_level: float
    red_avg_level: float
    win_prob: float
    win_prob_confidence: float
    teamfight_action: str = ""
    teamfight_prob: float = 0.0
    macro_action: str = ""
    feature_vector: Tuple[float, ...] = ()
    # Filled post-game
    actual_outcome: int = -1   # 1=blue win, 0=red win, -1=unknown

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ts": round(self.timestamp, 3),
            "game_time": round(self.game_time, 1),
            "phase": self.phase,
            "gold_diff": round(self.gold_diff, 0),
            "kill_diff": self.kill_diff,
            "tower_diff": self.tower_diff,
            "dragon_diff": self.dragon_diff,
            "blue_alive": self.blue_alive,
            "red_alive": self.red_alive,
            "blue_avg_lvl": round(self.blue_avg_level, 1),
            "red_avg_lvl": round(self.red_avg_level, 1),
            "win_prob": round(self.win_prob, 4),
            "win_conf": round(self.win_prob_confidence, 3),
            "tf_action": self.teamfight_action,
            "tf_prob": round(self.teamfight_prob, 3),
            "macro": self.macro_action,
            "outcome": self.actual_outcome,
        }
        if self.feature_vector:
            for i, v in enumerate(self.feature_vector):
                d[f"f{i}"] = round(v, 4)
        return d

    @property
    def csv_header(self) -> List[str]:
        base = [
            "ts", "game_time", "phase", "gold_diff", "kill_diff",
            "tower_diff", "dragon_diff", "blue_alive", "red_alive",
            "blue_avg_lvl", "red_avg_lvl", "win_prob", "win_conf",
            "tf_action", "tf_prob", "macro", "outcome",
        ]
        if self.feature_vector:
            base += [f"f{i}" for i in range(len(self.feature_vector))]
        return base


class TrainingDataCollector:
    """Collects per-tick training data and exports on game end.

    Usage::

        collector = TrainingDataCollector()
        collector.start_session("session_123")

        # In Proc() loop:
        collector.record(sample)

        # On game end:
        collector.set_outcome(1)  # blue won
        paths = collector.export()
        collector.end_session()
    """

    def __init__(
        self,
        data_dir: str = _DEFAULT_DATA_DIR,
        max_samples: int = _MAX_SAMPLES,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._max_samples = max_samples
        self._samples: Deque[TrainingSample] = deque(maxlen=max_samples)
        self._session_id: Optional[str] = None
        self._session_start: float = 0.0
        self._total_collected: int = 0
        self._total_exported: int = 0

    def start_session(self, session_id: str) -> None:
        """Begin collecting for a new game session."""
        self._session_id = session_id
        self._session_start = time.time()
        self._samples.clear()
        logger.info("TrainingData: session started — %s", session_id)

    def record(self, sample: TrainingSample) -> None:
        """Record a single training sample."""
        if self._session_id is None:
            return
        self._samples.append(sample)
        self._total_collected += 1

    def set_outcome(self, outcome: int) -> None:
        """Set the actual game outcome for all samples in this session.

        Args:
            outcome: 1 = blue win, 0 = red win
        """
        for i in range(len(self._samples)):
            s = self._samples[i]
            # TrainingSample is mutable, direct attribute set
            object.__setattr__(s, "actual_outcome", outcome)
        logger.info(
            "TrainingData: outcome set to %d for %d samples",
            outcome, len(self._samples),
        )

    def export(self) -> Dict[str, str]:
        """Export collected samples to JSONL and CSV files.

        Returns:
            Dict with 'jsonl' and 'csv' file paths.
        """
        if not self._samples:
            return {}

        self._data_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        sid = self._session_id or "unknown"
        base = f"{sid}_{ts}"

        # JSONL export
        jsonl_path = self._data_dir / f"{base}.jsonl"
        with open(jsonl_path, "w") as f:
            for sample in self._samples:
                f.write(json.dumps(sample.to_dict()) + "\n")

        # CSV export
        csv_path = self._data_dir / f"{base}.csv"
        if self._samples:
            header = self._samples[0].csv_header
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
                for sample in self._samples:
                    row = sample.to_dict()
                    # Only write fields in header
                    writer.writerow({k: row.get(k, "") for k in header})

        count = len(self._samples)
        self._total_exported += count
        logger.info(
            "TrainingData: exported %d samples → %s, %s",
            count, jsonl_path, csv_path,
        )

        return {
            "jsonl": str(jsonl_path),
            "csv": str(csv_path),
            "sample_count": count,
        }

    def end_session(self) -> None:
        """End the current collection session."""
        logger.info(
            "TrainingData: session ended — %d samples collected",
            len(self._samples),
        )
        self._session_id = None
        self._samples.clear()

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def stats(self) -> Dict[str, Any]:
        return {
            "session_id": self._session_id,
            "current_samples": len(self._samples),
            "total_collected": self._total_collected,
            "total_exported": self._total_exported,
            "max_samples": self._max_samples,
        }
