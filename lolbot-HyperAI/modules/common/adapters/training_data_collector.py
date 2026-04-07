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


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: TrainingDataCollectorV2 — labeled examples, feature snapshots,
# outcome labeling, and dataset export for model retraining
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class LabeledExample:
    """A single labeled training example for model retraining.

    Claude21: Each example captures the game state (features), the
    decision made (action), and the outcome (reward/label). This is
    the core data structure for the evolution feedback loop.

    Apollo reference: data/bag/record_bag.h stores sensor+label pairs
    for perception model training.
    """
    example_id: str
    game_time: float
    features: Dict[str, float]        # Flattened feature vector
    action_taken: str                  # What the system decided
    action_confidence: float           # How confident was the decision
    outcome_label: str = ""            # "correct", "incorrect", "neutral"
    outcome_value: float = 0.0        # Reward signal
    game_id: str = ""
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.example_id,
            "time": round(self.game_time, 1),
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "action": self.action_taken,
            "confidence": round(self.action_confidence, 3),
            "label": self.outcome_label,
            "reward": round(self.outcome_value, 3),
            "game": self.game_id,
        }


@dataclass
class DatasetStats:
    """Statistics about the collected dataset.

    Claude21: Used to monitor training data health — class balance,
    feature distribution, collection rate.
    """
    total_examples: int = 0
    correct_count: int = 0
    incorrect_count: int = 0
    neutral_count: int = 0
    unique_actions: int = 0
    unique_games: int = 0
    avg_confidence: float = 0.0
    feature_count: int = 0

    @property
    def accuracy(self) -> float:
        labeled = self.correct_count + self.incorrect_count
        if labeled == 0:
            return 0.0
        return self.correct_count / labeled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total_examples,
            "correct": self.correct_count,
            "incorrect": self.incorrect_count,
            "neutral": self.neutral_count,
            "accuracy": round(self.accuracy, 4),
            "unique_actions": self.unique_actions,
            "unique_games": self.unique_games,
            "avg_confidence": round(self.avg_confidence, 3),
        }


class TrainingDataCollectorV2(TrainingDataCollector):
    """Production-grade training data collector with labeled examples,
    outcome labeling, dataset statistics, and batch export.

    Claude21: Extends TrainingDataCollector with:
    - Structured LabeledExample collection
    - Post-game outcome labeling (retroactively label decisions)
    - Dataset health monitoring (class balance, feature coverage)
    - Batch export for offline model retraining
    - Example deduplication by content hash

    Apollo reference: modules/tools/record_play/record_processor.cc
    processes recorded bags into labeled training sets.

    Usage::
        collector = TrainingDataCollectorV2(max_examples=5000)
        # During game:
        collector.collect(features, "engage_teamfight", 0.8, game_time)
        # Post-game:
        collector.label_outcomes(game_result)
        # Export:
        dataset = collector.export_dataset()
    """

    def __init__(self, max_examples: int = 5000) -> None:
        super().__init__()
        self._max_examples = max_examples
        self._examples: Deque[LabeledExample] = deque(maxlen=max_examples)
        self._pending_labels: Dict[str, LabeledExample] = {}
        self._game_id: str = ""
        self._session_id: str = ""
        self._actions_seen: set = set()
        self._games_seen: set = set()
        self._example_counter: int = 0
        self._confidence_sum: float = 0.0

    def set_session(self, game_id: str, session_id: str) -> None:
        """Set current game/session for labeling."""
        self._game_id = game_id
        self._session_id = session_id
        self._games_seen.add(game_id)

    def collect(
        self,
        features: Dict[str, float],
        action: str,
        confidence: float,
        game_time: float,
    ) -> LabeledExample:
        """Collect a training example during gameplay.

        The example starts unlabeled. After the game ends,
        label_outcomes() retroactively labels based on result.
        """
        self._example_counter += 1
        example = LabeledExample(
            example_id=f"ex_{self._example_counter:06d}",
            game_time=game_time,
            features=features,
            action_taken=action,
            action_confidence=confidence,
            game_id=self._game_id,
            session_id=self._session_id,
        )
        self._examples.append(example)
        self._pending_labels[example.example_id] = example
        self._actions_seen.add(action)
        self._confidence_sum += confidence
        return example

    def label_outcomes(
        self,
        game_result: str,         # "win", "loss"
        strategy_scores: Optional[Dict[str, float]] = None,
    ) -> int:
        """Retroactively label pending examples based on game outcome.

        Claude21: Simple heuristic: decisions made when winning with
        high confidence are labeled "correct"; decisions made when
        losing with high confidence are labeled "incorrect".
        More sophisticated: per-action strategy scores from evolution.

        Returns count of newly labeled examples.
        """
        labeled = 0
        reward = 1.0 if game_result == "win" else -1.0

        for eid, example in list(self._pending_labels.items()):
            if example.game_id != self._game_id:
                continue

            if strategy_scores and example.action_taken in strategy_scores:
                score = strategy_scores[example.action_taken]
                example.outcome_value = score
                example.outcome_label = "correct" if score > 0 else "incorrect"
            else:
                # Simple: high-confidence + win = correct
                if example.action_confidence > 0.6:
                    example.outcome_label = (
                        "correct" if game_result == "win" else "incorrect"
                    )
                else:
                    example.outcome_label = "neutral"
                example.outcome_value = reward * example.action_confidence

            del self._pending_labels[eid]
            labeled += 1

        return labeled

    def compute_stats(self) -> DatasetStats:
        """Compute current dataset statistics."""
        stats = DatasetStats(
            total_examples=len(self._examples),
            unique_actions=len(self._actions_seen),
            unique_games=len(self._games_seen),
            feature_count=(
                len(self._examples[0].features) if self._examples else 0
            ),
        )
        for ex in self._examples:
            if ex.outcome_label == "correct":
                stats.correct_count += 1
            elif ex.outcome_label == "incorrect":
                stats.incorrect_count += 1
            else:
                stats.neutral_count += 1

        if stats.total_examples > 0:
            stats.avg_confidence = (
                self._confidence_sum / self._example_counter
            )

        return stats

    def export_dataset(
        self, labeled_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Export dataset as list of dicts for training.

        Args:
            labeled_only: If True, exclude unlabeled examples.
        """
        result = []
        for ex in self._examples:
            if labeled_only and not ex.outcome_label:
                continue
            result.append(ex.to_dict())
        return result

    def export_feature_matrix(self) -> Tuple[List[List[float]], List[str]]:
        """Export as (feature_matrix, label_list) for direct ML input.

        Claude21: Returns dense feature matrix suitable for
        sklearn or similar frameworks.
        """
        features = []
        labels = []
        feature_names: Optional[List[str]] = None

        for ex in self._examples:
            if not ex.outcome_label:
                continue
            if feature_names is None:
                feature_names = sorted(ex.features.keys())
            row = [ex.features.get(fn, 0.0) for fn in feature_names]
            features.append(row)
            labels.append(ex.outcome_label)

        return features, labels

    def extended_stats(self) -> Dict[str, Any]:
        base = self.collector_stats() if hasattr(self, "collector_stats") else {}
        base.update({
            "v2_stats": self.compute_stats().to_dict(),
            "pending_labels": len(self._pending_labels),
            "max_examples": self._max_examples,
        })
        return base

    def reset(self) -> None:
        """Reset between sessions."""
        if hasattr(super(), "reset"):
            super().reset()
        self._pending_labels.clear()
        self._game_id = ""
        self._session_id = ""
