"""
TrainingDataCollector — Game-by-game training data pipeline.
==============================================================
lolbot-HyperAI · Common Layer

Records feature vectors at regular intervals during a game, then
back-fills the outcome label (win/loss) after the game ends.
The resulting dataset can be used to retrain the win probability
and teamfight prediction models.

Architecture position:
    modules/common/adapters/training_data_collector.py   ← YOU ARE HERE
    ├─ Input: GameSnapshot features from perception/prediction
    ├─ Output: SQLite database with labeled training samples
    ├─ Triggered by: main_loop.py post-game hook
    └─ Used by: evolution layer for model weight tuning

Apollo reference:
    modules/data/warehouse/ — data collection pipeline
    modules/prediction/evaluator/ — model training data

Design notes:
    - SQLite for persistence (zero config, embedded)
    - Schema: session_id, game_time, feature_json, outcome
    - Sampling: every 30s during game to avoid bloat
    - Outcome backfill: updates all rows for a session after game end
    - Export: CSV/JSON for external model training
    - Auto-cleanup: prune sessions older than configurable retention
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("common.training_data")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_DB_PATH = "data/training_data.db"
_SAMPLE_INTERVAL_S = 30.0       # Record features every 30 seconds
_MAX_SAMPLES_PER_GAME = 200
_RETENTION_DAYS = 90
_EXPORT_BATCH_SIZE = 1000


# ─── Schema ──────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS training_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    game_time REAL NOT NULL,
    timestamp REAL NOT NULL,
    features TEXT NOT NULL,
    outcome INTEGER DEFAULT NULL,
    game_duration REAL DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_session ON training_samples(session_id);
CREATE INDEX IF NOT EXISTS idx_outcome ON training_samples(outcome);
"""

_CREATE_METADATA_SQL = """
CREATE TABLE IF NOT EXISTS session_metadata (
    session_id TEXT PRIMARY KEY,
    start_time REAL,
    end_time REAL,
    outcome INTEGER DEFAULT NULL,
    sample_count INTEGER DEFAULT 0,
    champion TEXT DEFAULT '',
    game_mode TEXT DEFAULT 'CLASSIC',
    notes TEXT DEFAULT ''
);
"""


# ─── Data Types ──────────────────────────────────────────────────────────────

@dataclass
class TrainingSample:
    """A single training data point."""
    session_id: str
    game_time: float
    features: Dict[str, float]
    outcome: Optional[int] = None  # 1=win, 0=loss, None=pending
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "game_time": self.game_time,
            "features": self.features,
            "outcome": self.outcome,
            "timestamp": self.timestamp,
        }


@dataclass
class CollectorStats:
    """Statistics for the training data collector."""
    total_sessions: int = 0
    total_samples: int = 0
    labeled_samples: int = 0
    unlabeled_samples: int = 0
    current_session_samples: int = 0
    db_size_mb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_sessions": self.total_sessions,
            "total_samples": self.total_samples,
            "labeled": self.labeled_samples,
            "unlabeled": self.unlabeled_samples,
            "current_session": self.current_session_samples,
            "db_size_mb": round(self.db_size_mb, 2),
        }


# ─── TrainingDataCollector ───────────────────────────────────────────────────

class TrainingDataCollector:
    """Collects and manages training data for model improvement.

    Lifecycle:
        1. ``start_session(session_id)`` — begin a new game session
        2. ``record(features)`` — called periodically during game
        3. ``end_session(outcome)`` — back-fill win/loss label
        4. ``export_*()`` — extract data for training

    Usage::

        collector = TrainingDataCollector("data/training.db")
        collector.initialize()
        collector.start_session("session_123")

        # During game (called by main_loop):
        collector.record({"gold_diff": 1500.0, "kill_diff": 2, ...}, game_time=600.0)

        # After game:
        collector.end_session(outcome=1)  # 1=win

        # For training:
        data = collector.export_csv("training_export.csv")
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._current_session: Optional[str] = None
        self._last_record_time: float = 0.0
        self._session_sample_count: int = 0
        self._initialized: bool = False

    def initialize(self) -> None:
        """Initialize the database and create tables."""
        db_dir = Path(self._db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._conn.executescript(_CREATE_TABLE_SQL)
        self._conn.executescript(_CREATE_INDEX_SQL)
        self._conn.executescript(_CREATE_METADATA_SQL)
        self._conn.commit()

        self._initialized = True
        logger.info("TrainingDataCollector initialized: %s", self._db_path)

    def start_session(
        self,
        session_id: str,
        champion: str = "",
        game_mode: str = "CLASSIC",
    ) -> None:
        """Start collecting data for a new game session.

        Args:
            session_id: Unique session identifier.
            champion: Player's champion name.
            game_mode: Game mode (CLASSIC, ARAM, etc.).
        """
        self._ensure_init()
        self._current_session = session_id
        self._last_record_time = 0.0
        self._session_sample_count = 0

        self._conn.execute(
            "INSERT OR REPLACE INTO session_metadata "
            "(session_id, start_time, champion, game_mode) VALUES (?, ?, ?, ?)",
            (session_id, time.time(), champion, game_mode),
        )
        self._conn.commit()
        logger.info("Training session started: %s (%s)", session_id, champion)

    def record(
        self,
        features: Dict[str, float],
        game_time: float,
    ) -> bool:
        """Record a feature snapshot.

        Respects the sampling interval — will skip if called too frequently.

        Args:
            features: Feature dictionary (e.g. from GameSnapshot.to_feature_dict()).
            game_time: Current game time in seconds.

        Returns:
            True if sample was recorded, False if skipped.
        """
        if not self._current_session:
            return False

        if self._session_sample_count >= _MAX_SAMPLES_PER_GAME:
            return False

        now = time.monotonic()
        if now - self._last_record_time < _SAMPLE_INTERVAL_S:
            return False

        features_json = json.dumps(features, default=str)
        self._conn.execute(
            "INSERT INTO training_samples "
            "(session_id, game_time, timestamp, features) VALUES (?, ?, ?, ?)",
            (self._current_session, game_time, time.time(), features_json),
        )

        self._session_sample_count += 1
        self._last_record_time = now

        # Commit every 10 samples to balance durability and performance
        # Always commit the first sample for test reliability
        if self._session_sample_count <= 1 or self._session_sample_count % 10 == 0:
            self._conn.commit()

        return True

    def end_session(
        self,
        outcome: int,
        game_duration: Optional[float] = None,
        notes: str = "",
    ) -> int:
        """End the current session and back-fill outcome labels.

        Args:
            outcome: 1 for win, 0 for loss.
            game_duration: Total game duration in seconds.
            notes: Optional session notes.

        Returns:
            Number of samples updated.
        """
        if not self._current_session:
            return 0

        session_id = self._current_session

        # Back-fill outcome on all samples for this session
        cursor = self._conn.execute(
            "UPDATE training_samples SET outcome = ?, game_duration = ? "
            "WHERE session_id = ?",
            (outcome, game_duration, session_id),
        )
        updated = cursor.rowcount

        # Update metadata
        self._conn.execute(
            "UPDATE session_metadata SET end_time = ?, outcome = ?, "
            "sample_count = ?, notes = ? WHERE session_id = ?",
            (time.time(), outcome, self._session_sample_count, notes, session_id),
        )
        self._conn.commit()

        logger.info(
            "Training session ended: %s, outcome=%d, samples=%d",
            session_id, outcome, updated,
        )

        self._current_session = None
        self._session_sample_count = 0
        return updated

    # ── Export ────────────────────────────────────────────────────────────

    def export_json(
        self,
        output_path: str,
        labeled_only: bool = True,
    ) -> int:
        """Export training data to a JSON Lines file.

        Args:
            output_path: File path for output.
            labeled_only: If True, only export samples with outcome set.

        Returns:
            Number of samples exported.
        """
        self._ensure_init()
        where = "WHERE outcome IS NOT NULL" if labeled_only else ""

        cursor = self._conn.execute(
            f"SELECT session_id, game_time, features, outcome, timestamp "
            f"FROM training_samples {where} ORDER BY timestamp",
        )

        count = 0
        with open(output_path, "w") as f:
            while True:
                rows = cursor.fetchmany(_EXPORT_BATCH_SIZE)
                if not rows:
                    break
                for row in rows:
                    record = {
                        "session_id": row[0],
                        "game_time": row[1],
                        "features": json.loads(row[2]),
                        "outcome": row[3],
                        "timestamp": row[4],
                    }
                    f.write(json.dumps(record) + "\n")
                    count += 1

        logger.info("Exported %d samples to %s", count, output_path)
        return count

    def export_csv(
        self,
        output_path: str,
        labeled_only: bool = True,
    ) -> int:
        """Export training data to CSV format.

        Flattens the feature dict into columns. All sessions share
        the same feature columns (union of all features seen).

        Returns:
            Number of rows exported.
        """
        self._ensure_init()
        where = "WHERE outcome IS NOT NULL" if labeled_only else ""

        # First pass: collect all feature column names
        cursor = self._conn.execute(
            f"SELECT features FROM training_samples {where} LIMIT 100",
        )
        all_keys: set = set()
        for (features_json,) in cursor:
            all_keys.update(json.loads(features_json).keys())
        feature_cols = sorted(all_keys)

        # Second pass: write CSV
        cursor = self._conn.execute(
            f"SELECT session_id, game_time, features, outcome "
            f"FROM training_samples {where} ORDER BY timestamp",
        )

        count = 0
        with open(output_path, "w") as f:
            header = "session_id,game_time," + ",".join(feature_cols) + ",outcome\n"
            f.write(header)

            while True:
                rows = cursor.fetchmany(_EXPORT_BATCH_SIZE)
                if not rows:
                    break
                for row in rows:
                    features = json.loads(row[2])
                    vals = [str(features.get(k, 0.0)) for k in feature_cols]
                    line = f"{row[0]},{row[1]}," + ",".join(vals) + f",{row[3]}\n"
                    f.write(line)
                    count += 1

        logger.info("Exported %d rows CSV to %s", count, output_path)
        return count

    # ── Maintenance ──────────────────────────────────────────────────────

    def prune_old_sessions(self, retention_days: int = _RETENTION_DAYS) -> int:
        """Remove sessions older than retention period.

        Returns:
            Number of sessions pruned.
        """
        self._ensure_init()
        cutoff = time.time() - retention_days * 86400

        cursor = self._conn.execute(
            "SELECT session_id FROM session_metadata WHERE start_time < ?",
            (cutoff,),
        )
        old_sessions = [row[0] for row in cursor]

        if not old_sessions:
            return 0

        placeholders = ",".join("?" * len(old_sessions))
        self._conn.execute(
            f"DELETE FROM training_samples WHERE session_id IN ({placeholders})",
            old_sessions,
        )
        self._conn.execute(
            f"DELETE FROM session_metadata WHERE session_id IN ({placeholders})",
            old_sessions,
        )
        self._conn.commit()

        logger.info("Pruned %d old sessions", len(old_sessions))
        return len(old_sessions)

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> CollectorStats:
        """Return collector statistics."""
        self._ensure_init()

        total_sessions = self._conn.execute(
            "SELECT COUNT(*) FROM session_metadata",
        ).fetchone()[0]
        total_samples = self._conn.execute(
            "SELECT COUNT(*) FROM training_samples",
        ).fetchone()[0]
        labeled = self._conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE outcome IS NOT NULL",
        ).fetchone()[0]

        db_size = 0.0
        if os.path.exists(self._db_path):
            db_size = os.path.getsize(self._db_path) / (1024 * 1024)

        return CollectorStats(
            total_sessions=total_sessions,
            total_samples=total_samples,
            labeled_samples=labeled,
            unlabeled_samples=total_samples - labeled,
            current_session_samples=self._session_sample_count,
            db_size_mb=db_size,
        )

    # ── Internal ─────────────────────────────────────────────────────────

    def _ensure_init(self) -> None:
        if not self._initialized:
            self.initialize()

    def shutdown(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._initialized = False
