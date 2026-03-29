"""
Training Data SQLite — Persist training samples to SQLite.

Provides table creation, sample insertion (single/batch), query by game_id,
random batch retrieval, epoch marking, stats aggregation, purge, and export.

Location: integrations/lol/src/lol_agent/training_data_sqlite.py

Reference (拿来主义):
  - DI-star/distar/agent/default/rl_learner.py: replay buffer persistence
  - Seraphine: match data storage patterns
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.training_data_sqlite.v1"


class TrainingDataSQLite:
    """SQLite-backed training sample store.

    Attributes:
        db_path: Path to SQLite database file.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, db_path: str = "training.db") -> None:
        self.db_path = db_path
        self.evolution_callback: Optional[Callable[[dict], None]] = None
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS training_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                state_vector TEXT NOT NULL,
                action INTEGER NOT NULL,
                reward REAL NOT NULL,
                epoch INTEGER DEFAULT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS epoch_log (
                epoch INTEGER PRIMARY KEY,
                marked_at REAL NOT NULL,
                sample_count INTEGER NOT NULL
            )
        """)
        self._conn.commit()

    def list_tables(self) -> list[str]:
        cur = self._conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cur.fetchall()]

    def insert_sample(self, sample: dict[str, Any]) -> int:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO training_samples (game_id, timestamp, state_vector, action, reward) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                sample["game_id"],
                sample["timestamp"],
                json.dumps(sample["state_vector"]),
                sample["action"],
                sample["reward"],
            ),
        )
        self._conn.commit()
        row_id = cur.lastrowid
        self._fire_evolution({"event": "sample_inserted", "row_id": row_id})
        return row_id

    def insert_batch(self, samples: list[dict[str, Any]]) -> int:
        cur = self._conn.cursor()
        rows = [
            (s["game_id"], s["timestamp"], json.dumps(s["state_vector"]), s["action"], s["reward"])
            for s in samples
        ]
        cur.executemany(
            "INSERT INTO training_samples (game_id, timestamp, state_vector, action, reward) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def count_samples(self) -> int:
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM training_samples")
        return cur.fetchone()[0]

    def query_by_game_id(self, game_id: str) -> list[dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM training_samples WHERE game_id = ?", (game_id,))
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def get_random_batch(self, batch_size: int = 16) -> list[dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM training_samples ORDER BY RANDOM() LIMIT ?", (batch_size,))
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def mark_epoch(self, epoch: int) -> None:
        count = self.count_samples()
        cur = self._conn.cursor()
        cur.execute("UPDATE training_samples SET epoch = ? WHERE epoch IS NULL", (epoch,))
        cur.execute(
            "INSERT OR REPLACE INTO epoch_log (epoch, marked_at, sample_count) VALUES (?, ?, ?)",
            (epoch, time.time(), count),
        )
        self._conn.commit()

    def get_epoch_stats(self, epoch: int) -> dict[str, Any]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM epoch_log WHERE epoch = ?", (epoch,))
        row = cur.fetchone()
        if row is None:
            return {"epoch": epoch, "sample_count": 0}
        return {"epoch": row["epoch"], "sample_count": row["sample_count"], "marked_at": row["marked_at"]}

    def aggregate_stats(self) -> dict[str, Any]:
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt, AVG(reward) as avg_r FROM training_samples")
        row = cur.fetchone()
        return {"total_samples": row["cnt"], "avg_reward": row["avg_r"] or 0.0}

    def purge_before(self, timestamp: float) -> int:
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM training_samples WHERE timestamp < ?", (timestamp,))
        count = cur.fetchone()[0]
        cur.execute("DELETE FROM training_samples WHERE timestamp < ?", (timestamp,))
        self._conn.commit()
        return count

    def export_all(self) -> list[dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM training_samples")
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        if "state_vector" in d and isinstance(d["state_vector"], str):
            d["state_vector"] = json.loads(d["state_vector"])
        return d

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
