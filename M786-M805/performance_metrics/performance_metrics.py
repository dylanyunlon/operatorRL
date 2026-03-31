#!/usr/bin/env python3
"""
M804: Performance Metrics
=========================
系统性能指标采集
查看现有实现方式,理解其模式,特别是代码和接口是如何分离的。
然后,遵循该模式实现新的 PerformanceMetricsManager,
让 OperatorRL 可以使用此模块,并能与其他模块协同工作。

Reference: operatorRL agentic system / Seraphine LCU patterns
"""

import os, sys, json, time, math, hashlib, sqlite3, threading, logging, struct, re
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union, Set, Callable
from dataclasses import dataclass, field, asdict
from collections import defaultdict, Counter, OrderedDict, deque

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from logging_system.core_logger import get_logger, EventCategory
except ImportError:
    get_logger = lambda x: logging.getLogger(x)
    EventCategory = type('E', (), dict(SYSTEM='system', DATA='data',
        NETWORK='network', PERF='performance'))()


# ============================================================================
# Constants & Configuration
# ============================================================================

MAX_BUFFER_SIZE = 10000
PROCESSING_INTERVAL_MS = 100
DEFAULT_TIMEOUT_SEC = 30
RETRY_MAX = 3
HEALTH_CHECK_INTERVAL = 60
CACHE_TTL_SEC = 300
MAX_HISTORY_SIZE = 5000
BATCH_PROCESS_SIZE = 50


class PerformanceMetricsStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    STOPPED = "stopped"
    INITIALIZING = "initializing"
    DEGRADED = "degraded"


class EventPriority(Enum):
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    INFO = 5
    DEBUG = 6


class ProcessingMode(Enum):
    REALTIME = "realtime"
    BATCH = "batch"
    HYBRID = "hybrid"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class PerformanceMetricsConfig:
    enabled: bool = True
    buffer_size: int = MAX_BUFFER_SIZE
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    retry_max: int = RETRY_MAX
    auto_start: bool = False
    log_level: str = "INFO"
    processing_mode: ProcessingMode = ProcessingMode.REALTIME
    cache_ttl_sec: int = CACHE_TTL_SEC
    batch_size: int = BATCH_PROCESS_SIZE
    custom_params: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'PerformanceMetricsConfig':
        mode = data.pop("processing_mode", "realtime")
        if isinstance(mode, str):
            data["processing_mode"] = ProcessingMode(mode)
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})


@dataclass
class PerformanceMetricsEvent:
    event_id: str = ""
    event_type: str = ""
    priority: EventPriority = EventPriority.MEDIUM
    timestamp: float = 0.0
    data: Dict = field(default_factory=dict)
    source: str = ""
    processed: bool = False
    processing_time_ms: float = 0.0
    retry_count: int = 0
    correlation_id: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = hashlib.sha256(
                f"{self.event_type}:{self.timestamp}:{id(self)}".encode()
            ).hexdigest()[:16]
        if not self.correlation_id:
            self.correlation_id = hashlib.sha256(
                f"corr:{self.event_id}".encode()
            ).hexdigest()[:12]


@dataclass
class PerformanceMetricsMetrics:
    total_events: int = 0
    processed_events: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    uptime_sec: float = 0.0
    last_event_time: float = 0.0
    queue_depth: int = 0
    events_per_second: float = 0.0
    cache_hit_rate: float = 0.0
    memory_usage_mb: float = 0.0


@dataclass
class PerformanceMetricsSnapshot:
    snapshot_id: str = ""
    timestamp: float = 0.0
    status: str = "unknown"
    metrics: Dict = field(default_factory=dict)
    config: Dict = field(default_factory=dict)
    health: Dict = field(default_factory=dict)


# ============================================================================
# Event Bus
# ============================================================================

class PerformanceMetricsEventBus:
    """Internal event bus for PerformanceMetrics module communication."""

    def __init__(self, logger=None):
        self._logger = logger
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._event_history: deque = deque(maxlen=MAX_HISTORY_SIZE)
        self._lock = threading.Lock()
        self._publish_count = 0
        self._delivery_failures = 0

    def subscribe(self, event_type: str, handler: Callable):
        with self._lock:
            self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type] = [
                    h for h in self._subscribers[event_type] if h != handler
                ]

    def publish(self, event: PerformanceMetricsEvent):
        with self._lock:
            self._event_history.append(event)
            handlers = list(self._subscribers.get(event.event_type, []))
            handlers.extend(self._subscribers.get("*", []))
            self._publish_count += 1

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                self._delivery_failures += 1
                if self._logger:
                    self._logger.error(f"Event delivery failed: {e}")

    def get_history(self, event_type: str = None, limit: int = 50) -> List[Dict]:
        with self._lock:
            events = list(self._event_history)
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return [asdict(e) for e in events[-limit:]]

    @property
    def subscriber_count(self):
        return sum(len(v) for v in self._subscribers.values())

    @property
    def stats(self):
        return {
            "published": self._publish_count,
            "delivery_failures": self._delivery_failures,
            "subscribers": self.subscriber_count,
            "history_size": len(self._event_history),
        }


# ============================================================================
# Storage Layer
# ============================================================================

class PerformanceMetricsStorage:
    """Persistent storage for PerformanceMetrics data with caching."""

    def __init__(self, db_path: Path, logger=None):
        self._logger = logger
        self._db_path = db_path
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_ttl = CACHE_TTL_SEC
        self._write_count = 0
        self._read_count = 0
        self._cache_hits = 0
        self._init_db()

    def _init_db(self):
        os.makedirs(self._db_path.parent, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS performance_metrics_events (
            event_id TEXT PRIMARY KEY, event_type TEXT, priority INTEGER,
            timestamp REAL, data_json TEXT, source TEXT, processed INTEGER,
            processing_time_ms REAL, correlation_id TEXT, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS performance_metrics_config (
            key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS performance_metrics_snapshots (
            snapshot_id TEXT PRIMARY KEY, timestamp REAL,
            snapshot_json TEXT, created_at TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_performance_metrics_ts ON performance_metrics_events(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_performance_metrics_type ON performance_metrics_events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_performance_metrics_corr ON performance_metrics_events(correlation_id)")
        conn.commit()
        conn.close()

    def store_event(self, event: PerformanceMetricsEvent):
        self._write_count += 1
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT OR REPLACE INTO performance_metrics_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (event.event_id, event.event_type, event.priority.value,
                 event.timestamp, json.dumps(event.data, default=str),
                 event.source, 1 if event.processed else 0,
                 event.processing_time_ms, event.correlation_id,
                 datetime.now(timezone.utc).isoformat()))
            conn.commit()
            conn.close()
            cache_key = f"event:{event.event_id}"
            self._cache[cache_key] = (time.time(), asdict(event))
        except Exception as e:
            if self._logger:
                self._logger.error(f"Store event error: {e}")

    def get_event(self, event_id: str) -> Optional[Dict]:
        self._read_count += 1
        cache_key = f"event:{event_id}"
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                self._cache_hits += 1
                return data
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT data_json, event_type, timestamp FROM performance_metrics_events WHERE event_id=?",
            (event_id,)).fetchone()
        conn.close()
        if row:
            result = {"data": json.loads(row[0]), "type": row[1], "timestamp": row[2]}
            self._cache[cache_key] = (time.time(), result)
            return result
        return None

    def get_events(self, event_type: str = None, limit: int = 100) -> List[Dict]:
        self._read_count += 1
        conn = sqlite3.connect(str(self._db_path))
        if event_type:
            rows = conn.execute(
                "SELECT data_json FROM performance_metrics_events WHERE event_type=? ORDER BY timestamp DESC LIMIT ?",
                (event_type, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT data_json FROM performance_metrics_events ORDER BY timestamp DESC LIMIT ?",
                (limit,)).fetchall()
        conn.close()
        return [json.loads(r[0]) for r in rows]

    def store_snapshot(self, snapshot: PerformanceMetricsSnapshot):
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT OR REPLACE INTO performance_metrics_snapshots VALUES (?,?,?,?)",
                (snapshot.snapshot_id, snapshot.timestamp,
                 json.dumps(asdict(snapshot), default=str),
                 datetime.now(timezone.utc).isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            if self._logger:
                self._logger.error(f"Snapshot store error: {e}")

    def store_config(self, key: str, value: str):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("INSERT OR REPLACE INTO performance_metrics_config VALUES (?,?,?)",
            (key, value, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

    def get_config(self, key: str) -> Optional[str]:
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute("SELECT value FROM performance_metrics_config WHERE key=?", (key,)).fetchone()
        conn.close()
        return row[0] if row else None

    def cleanup_old_events(self, max_age_sec: int = 86400 * 7):
        cutoff = time.time() - max_age_sec
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("DELETE FROM performance_metrics_events WHERE timestamp < ?", (cutoff,))
        conn.commit()
        conn.close()

    @property
    def stats(self):
        hit_rate = (self._cache_hits / max(self._read_count, 1)) * 100
        return {"writes": self._write_count, "reads": self._read_count,
                "cache_hits": self._cache_hits, "cache_hit_rate": round(hit_rate, 1),
                "cache_size": len(self._cache)}


# ============================================================================
# Processor
# ============================================================================

class PerformanceMetricsProcessor:
    """Core processing logic for PerformanceMetrics."""

    def __init__(self, config: PerformanceMetricsConfig, logger=None):
        self._config = config
        self._logger = logger
        self._buffer: deque = deque(maxlen=config.buffer_size)
        self._processed = 0
        self._errors = 0
        self._latencies: deque = deque(maxlen=1000)
        self._lock = threading.Lock()

    def process(self, data: Dict) -> Tuple[bool, Dict]:
        start = time.time()
        try:
            result = self._transform(data)
            if not self._validate(result):
                self._errors += 1
                return False, {"error": "Validation failed", "data": data}
            enriched = self._enrich(result)
            latency = round((time.time() - start) * 1000, 2)
            self._latencies.append(latency)
            self._processed += 1
            return True, {**enriched, "_latency_ms": latency}
        except Exception as e:
            self._errors += 1
            if self._logger:
                self._logger.error(f"Processing error: {e}")
            return False, {"error": str(e), "data": data}

    def process_batch(self, items: List[Dict]) -> List[Tuple[bool, Dict]]:
        return [self.process(item) for item in items]

    def _transform(self, data: Dict) -> Dict:
        transformed = {k: v for k, v in data.items() if v is not None}
        transformed["_processed_at"] = datetime.now(timezone.utc).isoformat()
        transformed["_module"] = "M804"
        transformed["_version"] = "1.0"
        return transformed

    def _validate(self, data: Dict) -> bool:
        if not data:
            return False
        if "_processed_at" not in data:
            return False
        return True

    def _enrich(self, data: Dict) -> Dict:
        data["_enriched"] = True
        data["_hash"] = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        data["_size_bytes"] = len(json.dumps(data, default=str).encode())
        return data

    def buffer(self, data: Dict):
        with self._lock:
            self._buffer.append(data)

    def flush(self) -> List[Dict]:
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
        return items

    def get_p99_latency(self) -> float:
        if not self._latencies:
            return 0.0
        sorted_l = sorted(self._latencies)
        idx = int(len(sorted_l) * 0.99)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def stats(self):
        avg_lat = (sum(self._latencies) / len(self._latencies)) if self._latencies else 0
        return {"processed": self._processed, "errors": self._errors,
                "buffer_depth": len(self._buffer),
                "avg_latency_ms": round(avg_lat, 2),
                "p99_latency_ms": round(self.get_p99_latency(), 2)}


# ============================================================================
# Health Checker
# ============================================================================

class PerformanceMetricsHealthChecker:
    """Health monitoring for PerformanceMetrics module."""

    def __init__(self, logger=None):
        self._logger = logger
        self._checks: List[Tuple[str, Callable]] = []
        self._last_check: Dict = {}
        self._check_count = 0

    def register_check(self, name: str, check_fn: Callable):
        self._checks.append((name, check_fn))

    def run_checks(self) -> Dict:
        self._check_count += 1
        results = {}
        overall_healthy = True
        for name, check_fn in self._checks:
            try:
                healthy = check_fn()
                results[name] = {"healthy": healthy, "error": None}
                if not healthy:
                    overall_healthy = False
            except Exception as e:
                results[name] = {"healthy": False, "error": str(e)}
                overall_healthy = False

        self._last_check = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_healthy": overall_healthy,
            "checks": results,
            "check_number": self._check_count,
        }
        return self._last_check

    @property
    def last_check(self):
        return self._last_check

    @property
    def is_healthy(self):
        return self._last_check.get("overall_healthy", True)


# ============================================================================
# Main Manager
# ============================================================================

class PerformanceMetricsManager:
    """
    Primary Performance Metrics engine for operatorRL M786-M805.
    Coordinates EventBus, Storage, Processor, and HealthChecker
    for production-grade performance metrics management.
    """

    def __init__(self, config: Optional[PerformanceMetricsConfig] = None,
                 db_path: Optional[Path] = None, logger=None):
        self._logger = logger or (
            get_logger("M804") if callable(get_logger)
            else logging.getLogger("M804"))
        self._config = config or PerformanceMetricsConfig()
        self._db_path = db_path or Path(__file__).parent / "performance_metrics.db"
        self._status = PerformanceMetricsStatus.INITIALIZING
        self._event_bus = PerformanceMetricsEventBus(self._logger)
        self._storage = PerformanceMetricsStorage(self._db_path, self._logger)
        self._processor = PerformanceMetricsProcessor(self._config, self._logger)
        self._health = PerformanceMetricsHealthChecker(self._logger)
        self._metrics = PerformanceMetricsMetrics()
        self._start_time = time.time()
        self._lock = threading.Lock()
        self._snapshot_history: List[PerformanceMetricsSnapshot] = []
        self._setup_health_checks()
        self._status = PerformanceMetricsStatus.IDLE

    def _setup_health_checks(self):
        self._health.register_check(
            "status", lambda: self._status not in (
                PerformanceMetricsStatus.ERROR, PerformanceMetricsStatus.STOPPED))
        self._health.register_check(
            "database", lambda: self._db_path.parent.exists())
        self._health.register_check(
            "error_rate", lambda: self._processor.stats["errors"] <
            max(self._processor.stats["processed"] * 0.1, 100))
        self._health.register_check(
            "buffer", lambda: self._processor.stats["buffer_depth"] <
            self._config.buffer_size * 0.9)

    def start(self):
        if self._status in (PerformanceMetricsStatus.RUNNING,):
            return
        self._status = PerformanceMetricsStatus.RUNNING
        self._start_time = time.time()
        self._publish_lifecycle("started")

    def stop(self):
        if self._status == PerformanceMetricsStatus.STOPPED:
            return
        remaining = self._processor.flush()
        for item in remaining:
            self._storage.store_event(PerformanceMetricsEvent(
                event_type="flushed", timestamp=time.time(),
                data=item, source="M804"))
        self._status = PerformanceMetricsStatus.STOPPED
        self._publish_lifecycle("stopped")

    def pause(self):
        self._status = PerformanceMetricsStatus.PAUSED
        self._publish_lifecycle("paused")

    def resume(self):
        if self._status == PerformanceMetricsStatus.PAUSED:
            self._status = PerformanceMetricsStatus.RUNNING
            self._publish_lifecycle("resumed")

    def process(self, data: Dict) -> Dict:
        if self._status != PerformanceMetricsStatus.RUNNING:
            return {"error": f"Module not running (status: {self._status.value})",
                    "status": self._status.value}

        success, result = self._processor.process(data)
        with self._lock:
            self._metrics.total_events += 1
            if success:
                self._metrics.processed_events += 1
                self._metrics.avg_latency_ms = self._processor.stats["avg_latency_ms"]
                self._metrics.p99_latency_ms = self._processor.stats["p99_latency_ms"]
            else:
                self._metrics.error_count += 1
            self._metrics.last_event_time = time.time()
            elapsed = time.time() - self._start_time
            self._metrics.uptime_sec = round(elapsed, 1)
            self._metrics.events_per_second = round(
                self._metrics.total_events / max(elapsed, 1), 2)

        if success:
            event = PerformanceMetricsEvent(
                event_type="processed", timestamp=time.time(),
                data=result, source="M804", processed=True,
                processing_time_ms=result.get("_latency_ms", 0))
            self._event_bus.publish(event)
            self._storage.store_event(event)

        return result

    def process_batch(self, items: List[Dict]) -> List[Dict]:
        results = []
        for item in items:
            results.append(self.process(item))
        return results

    def take_snapshot(self) -> PerformanceMetricsSnapshot:
        snapshot = PerformanceMetricsSnapshot(
            snapshot_id=hashlib.sha256(
                f"snap:{time.time()}".encode()).hexdigest()[:16],
            timestamp=time.time(),
            status=self._status.value,
            metrics=self.get_metrics(),
            config=self._config.to_dict(),
            health=self.get_health())
        self._snapshot_history.append(snapshot)
        self._storage.store_snapshot(snapshot)
        return snapshot

    def get_metrics(self) -> Dict:
        with self._lock:
            self._metrics.queue_depth = self._processor.stats["buffer_depth"]
            self._metrics.cache_hit_rate = self._storage.stats["cache_hit_rate"]
            return asdict(self._metrics)

    def get_health(self) -> Dict:
        return self._health.run_checks()

    def subscribe(self, event_type: str, handler: Callable):
        self._event_bus.subscribe(event_type, handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        self._event_bus.unsubscribe(event_type, handler)

    def get_event_history(self, event_type: str = None,
                          limit: int = 50) -> List[Dict]:
        return self._event_bus.get_history(event_type, limit)

    def get_stored_events(self, event_type: str = None,
                          limit: int = 100) -> List[Dict]:
        return self._storage.get_events(event_type, limit)

    def set_config(self, key: str, value: str):
        self._storage.store_config(key, value)

    def get_config(self, key: str) -> Optional[str]:
        return self._storage.get_config(key)

    def cleanup(self, max_age_sec: int = 86400 * 7):
        self._storage.cleanup_old_events(max_age_sec)

    def _publish_lifecycle(self, action: str):
        self._event_bus.publish(PerformanceMetricsEvent(
            event_type="lifecycle", priority=EventPriority.HIGH,
            timestamp=time.time(),
            data={"action": action, "module": "M804",
                  "status": self._status.value},
            source="M804"))

    @property
    def status(self) -> PerformanceMetricsStatus:
        return self._status

    @property
    def event_bus(self) -> PerformanceMetricsEventBus:
        return self._event_bus

    @property
    def storage(self) -> PerformanceMetricsStorage:
        return self._storage


# ============================================================================
# Module Self-Test
# ============================================================================

def _self_test():
    print("[M804] PerformanceMetricsManager self-test...")

    # Test config
    config = PerformanceMetricsConfig(buffer_size=100, timeout_sec=10)
    assert config.enabled
    assert config.buffer_size == 100

    # Test manager lifecycle
    manager = PerformanceMetricsManager(
        config=config,
        db_path=Path("/tmp/test_performance_metrics.db"))
    assert manager.status == PerformanceMetricsStatus.IDLE

    manager.start()
    assert manager.status == PerformanceMetricsStatus.RUNNING

    # Test event processing
    events_received = []
    manager.subscribe("processed", lambda e: events_received.append(e))

    result = manager.process({"test_key": "test_value", "numeric": 42})
    assert "_hash" in result or "error" not in result

    # Process batch
    batch_results = manager.process_batch([
        {"item": i, "data": f"batch_{i}"} for i in range(5)
    ])
    assert len(batch_results) == 5

    # Check metrics
    metrics = manager.get_metrics()
    assert metrics["total_events"] == 6
    assert metrics["processed_events"] >= 5

    # Check health
    health = manager.get_health()
    assert health["overall_healthy"]

    # Test config storage
    manager.set_config("test_param", "test_value")
    assert manager.get_config("test_param") == "test_value"

    # Test snapshot
    snapshot = manager.take_snapshot()
    assert snapshot.status == "running"

    # Test pause/resume
    manager.pause()
    assert manager.status == PerformanceMetricsStatus.PAUSED
    pause_result = manager.process({"should": "fail"})
    assert "error" in pause_result
    manager.resume()
    assert manager.status == PerformanceMetricsStatus.RUNNING

    # Test cleanup
    manager.cleanup()

    # Stop
    manager.stop()
    assert manager.status == PerformanceMetricsStatus.STOPPED

    print(f"  Events processed: {metrics['processed_events']}")
    print(f"  Errors: {metrics['error_count']}")
    print(f"  Events/sec: {metrics['events_per_second']}")
    print(f"  Avg latency: {metrics['avg_latency_ms']}ms")
    print(f"  Health: {health['overall_healthy']}")
    print(f"  Event bus stats: {manager.event_bus.stats}")
    print(f"  Storage stats: {manager.storage.stats}")
    print("[M804] All tests passed.\n")
    return True


if __name__ == "__main__":
    _self_test()
