#!/usr/bin/env python3
"""
M904 — DataPersistenceManager
===============================
Unified SQLite persistence for all module data with backup/migration.

Reference: connector.py session management
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M904.DataPersistenceManager")


SCHEMA_VERSION = 3
DEFAULT_DB_PATH = "operatorrl_m886_m905.db"


class DataPersistenceManager:
    """
    Unified SQLite persistence for all M886-M905 module data.
    Handles schema migration, auto-backup, and crash recovery.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._stats = {"reads": 0, "writes": 0, "backups": 0}
        logger.info("DataPersistenceManager initialized (db=%s)", db_path)

    def connect(self):
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        logger.info("Database connected")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_schema(self):
        if not self._conn: return
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS kv_store (
                namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY (namespace, key)
            );
            CREATE TABLE IF NOT EXISTS match_history (
                game_id TEXT PRIMARY KEY, puuid TEXT, data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY, game_id TEXT, category TEXT,
                feedback_type TEXT, score REAL, data TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS model_weights (
                version INTEGER PRIMARY KEY AUTOINCREMENT,
                weights TEXT NOT NULL, loss REAL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
            INSERT OR IGNORE INTO schema_version VALUES (%d);
        """ % SCHEMA_VERSION)
        self._conn.commit()

    def put(self, namespace: str, key: str, value: Any):
        if not self._conn: return
        data = json.dumps(value, ensure_ascii=False, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO kv_store (namespace, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (namespace, key, data, datetime.now(timezone.utc).isoformat())
        )
        self._conn.commit()
        self._stats["writes"] += 1

    def get(self, namespace: str, key: str) -> Optional[Any]:
        if not self._conn: return None
        cursor = self._conn.execute(
            "SELECT value FROM kv_store WHERE namespace=? AND key=?", (namespace, key)
        )
        row = cursor.fetchone()
        self._stats["reads"] += 1
        return json.loads(row[0]) if row else None

    def list_keys(self, namespace: str) -> List[str]:
        if not self._conn: return []
        cursor = self._conn.execute(
            "SELECT key FROM kv_store WHERE namespace=?", (namespace,)
        )
        return [row[0] for row in cursor.fetchall()]

    def save_match(self, game_id: str, puuid: str, data: Dict):
        if not self._conn: return
        self._conn.execute(
            "INSERT OR REPLACE INTO match_history (game_id, puuid, data, created_at) VALUES (?, ?, ?, ?)",
            (game_id, puuid, json.dumps(data), datetime.now(timezone.utc).isoformat())
        )
        self._conn.commit()
        self._stats["writes"] += 1

    def get_match(self, game_id: str) -> Optional[Dict]:
        if not self._conn: return None
        cursor = self._conn.execute("SELECT data FROM match_history WHERE game_id=?", (game_id,))
        row = cursor.fetchone()
        self._stats["reads"] += 1
        return json.loads(row[0]) if row else None

    def save_feedback(self, entry: Dict):
        if not self._conn: return
        self._conn.execute(
            "INSERT OR REPLACE INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry.get("id"), entry.get("game"), entry.get("category"),
             entry.get("type"), entry.get("score", 0), json.dumps(entry),
             datetime.now(timezone.utc).isoformat())
        )
        self._conn.commit()
        self._stats["writes"] += 1

    def save_weights(self, weights: Dict[str, float], loss: float):
        if not self._conn: return
        self._conn.execute(
            "INSERT INTO model_weights (weights, loss, created_at) VALUES (?, ?, ?)",
            (json.dumps(weights), loss, datetime.now(timezone.utc).isoformat())
        )
        self._conn.commit()
        self._stats["writes"] += 1

    def get_latest_weights(self) -> Optional[Dict[str, float]]:
        if not self._conn: return None
        cursor = self._conn.execute(
            "SELECT weights FROM model_weights ORDER BY version DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def backup(self, backup_path: Optional[str] = None):
        if not self._conn: return
        bp = backup_path or f"{self._db_path}.bak.{int(time.time())}"
        backup_conn = sqlite3.connect(bp)
        self._conn.backup(backup_conn)
        backup_conn.close()
        self._stats["backups"] += 1
        logger.info("Database backed up to %s", bp)

    def export_stats(self) -> Dict[str, Any]:
        size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0
        return {"persistence_stats": self._stats, "db_size_bytes": size,
                "schema_version": SCHEMA_VERSION}



# ---------------------------------------------------------------------------
# Extended DataPersistenceManager utilities
# ---------------------------------------------------------------------------

class MigrationManager:
    """Handles database schema migrations between versions."""

    MIGRATIONS = {
        2: [
            "ALTER TABLE kv_store ADD COLUMN ttl_seconds INTEGER DEFAULT 0",
            "CREATE INDEX IF NOT EXISTS idx_kv_namespace ON kv_store(namespace)",
        ],
        3: [
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, data TEXT, created_at TEXT, ended_at TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_match_puuid ON match_history(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_game ON feedback(game_id)",
        ],
    }

    @classmethod
    def get_current_version(cls, conn: sqlite3.Connection) -> int:
        try:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            row = cursor.fetchone()
            return row[0] if row and row[0] else 1
        except sqlite3.OperationalError:
            return 1

    @classmethod
    def migrate(cls, conn: sqlite3.Connection, target_version: int) -> List[str]:
        current = cls.get_current_version(conn)
        applied = []

        for version in range(current + 1, target_version + 1):
            statements = cls.MIGRATIONS.get(version, [])
            for sql in statements:
                try:
                    conn.execute(sql)
                    applied.append(f"v{version}: {sql[:60]}...")
                except sqlite3.OperationalError as exc:
                    logger.warning("Migration v%d skipped: %s", version, exc)

            conn.execute("INSERT OR REPLACE INTO schema_version VALUES (?)", (version,))

        conn.commit()
        if applied:
            logger.info("Applied %d migrations to v%d", len(applied), target_version)
        return applied


class QueryBuilder:
    """Simple query builder for common data access patterns."""

    @staticmethod
    def select(table: str, where: Optional[Dict[str, Any]] = None,
               order_by: Optional[str] = None, limit: Optional[int] = None) -> Tuple[str, List]:
        sql = f"SELECT * FROM {table}"
        params = []
        if where:
            clauses = []
            for key, val in where.items():
                clauses.append(f"{key} = ?")
                params.append(val)
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit:
            sql += f" LIMIT {limit}"
        return sql, params

    @staticmethod
    def insert(table: str, data: Dict[str, Any]) -> Tuple[str, List]:
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        sql = f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})"
        return sql, list(data.values())

    @staticmethod
    def count(table: str, where: Optional[Dict[str, Any]] = None) -> Tuple[str, List]:
        sql = f"SELECT COUNT(*) FROM {table}"
        params = []
        if where:
            clauses = []
            for key, val in where.items():
                clauses.append(f"{key} = ?")
                params.append(val)
            sql += " WHERE " + " AND ".join(clauses)
        return sql, params


class CacheLayer:
    """In-memory cache layer over SQLite for frequently accessed data."""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._max = max_size
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._cache[key]
            self._misses += 1
            return None
        self._hits += 1
        return value

    def put(self, key: str, value: Any):
        if len(self._cache) >= self._max:
            # Evict oldest
            oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
        self._cache[key] = (value, time.monotonic())

    def invalidate(self, key: str):
        self._cache.pop(key, None)

    def clear(self):
        self._cache.clear()

    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1) * 100, 1),
        }


class DataExporter:
    """Exports database contents to various formats."""

    @staticmethod
    def export_to_json(conn: sqlite3.Connection, table: str,
                       output_path: str) -> int:
        cursor = conn.execute(f"SELECT * FROM {table}")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        data = [dict(zip(columns, row)) for row in rows]
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return len(data)

    @staticmethod
    def export_to_csv(conn: sqlite3.Connection, table: str,
                      output_path: str) -> int:
        import csv
        cursor = conn.execute(f"SELECT * FROM {table}")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
        return len(rows)

    @staticmethod
    def get_table_sizes(conn: sqlite3.Connection) -> Dict[str, int]:
        tables = ["kv_store", "match_history", "feedback", "model_weights"]
        sizes = {}
        for t in tables:
            try:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {t}")
                sizes[t] = cursor.fetchone()[0]
            except sqlite3.OperationalError:
                sizes[t] = 0
        return sizes


class AutoBackupScheduler:
    """Schedules automatic database backups."""

    def __init__(self, persistence, backup_dir: str = "backups",
                 interval_hours: int = 6, max_backups: int = 10):
        self._persistence = persistence
        self._backup_dir = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._interval = interval_hours * 3600
        self._max_backups = max_backups
        self._last_backup: float = 0

    def should_backup(self) -> bool:
        return time.monotonic() - self._last_backup >= self._interval

    def run_backup(self) -> Optional[str]:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = str(self._backup_dir / f"operatorrl_backup_{ts}.db")
        self._persistence.backup(backup_path)
        self._last_backup = time.monotonic()
        self._cleanup_old_backups()
        return backup_path

    def _cleanup_old_backups(self):
        backups = sorted(self._backup_dir.glob("operatorrl_backup_*.db"))
        while len(backups) > self._max_backups:
            old = backups.pop(0)
            old.unlink(missing_ok=True)



# ---------------------------------------------------------------------------
# Extended DataPersistenceManager utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class DataPersistenceManagerMetrics:
    """Collects performance metrics for DataPersistenceManager."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class DataPersistenceManagerSerializer:
    """Serialization utilities for DataPersistenceManager state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class DataPersistenceManagerDiagnostics:
    """Diagnostic tools for DataPersistenceManager troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "DataPersistenceManager",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class DataPersistenceManagerEventLogger:
    """Structured event logger for DataPersistenceManager with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)
