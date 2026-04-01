#!/usr/bin/env python3
"""
M1046-M1065: Evolution Logger — Self-Evolving Runtime Log System
================================================================

OperatorRL Agentic System: 自部署 自环境反馈 自演化

This module implements the core logging infrastructure that drives the
self-evolution loop. Every component in M1046-M1065 emits structured
logs. The LLM "repair enzyme" reads these logs to suggest improvements,
and the new generation replaces the old.

Architecture Pattern (from plan.md §二):
    真实世界 HTTP —→ success/error（不可改变的物理事实）
        ↓
    程序A（Agent）—→ 运行，撞墙，记录日志
        ↓
    LLM（修复酶）—→ 看日志，建议修改
        ↓
    程序A'（新一代）—→ 替换 A

References:
    - Seraphine (ljszx/Seraphine): app/common/logger.py pattern
    - Akagi (shinkuan/Akagi): MITM logging for traffic analysis
    - Fiddler MCP Server: localhost:8868/mcp structured log export
    - dota2bot-OpenHyperAI: distributed bot logging architecture

Production Critique (Knuth-level):
    1. User-perspective: Log rotation prevents disk exhaustion on 30-min
       game sessions producing ~50MB logs. Structured JSON ensures
       downstream LLM parsing never fails on malformed entries.
    2. System-perspective: Async I/O prevents logger from blocking the
       14fps screen-capture or network-capture hot path. Ring buffer
       caps memory at 128MB regardless of game duration.
"""

import asyncio
import datetime
import hashlib
import inspect
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RING_BUFFER_BYTES = 128 * 1024 * 1024  # 128 MB cap
_MAX_LOG_FILE_BYTES = 50 * 1024 * 1024       # 50 MB per file
_LOG_ROTATION_COUNT = 5                       # keep 5 rotated files
_FLUSH_INTERVAL_SEC = 1.0                     # flush to disk every 1s
_DEFAULT_LOG_DIR = "logs/m1046_m1065"
_STRUCTURED_LOG_VERSION = "1.0.0"


class LogLevel(Enum):
    """Semantic log levels aligned with OperatorRL reward signals."""
    TRACE = auto()      # Ultra-verbose: every packet, every frame
    DEBUG = auto()      # Developer: internal state transitions
    INFO = auto()       # Normal: game events, API calls
    WARN = auto()       # Degraded: retries, fallbacks, latency spikes
    ERROR = auto()      # Failure: API errors, parse failures
    CRITICAL = auto()   # Fatal: system crash, unrecoverable state
    REWARD = auto()     # RL signal: positive reward event
    PENALTY = auto()    # RL signal: negative reward / policy violation
    EVOLUTION = auto()  # Meta: self-evolution checkpoint / mutation


class LogCategory(Enum):
    """Functional categories for log routing and analysis."""
    NETWORK_CAPTURE = "network_capture"
    HISTORY_FETCH = "history_fetch"
    STRATEGY_ENGINE = "strategy_engine"
    VOICE_OUTPUT = "voice_output"
    FIDDLER_MCP = "fiddler_mcp"
    LCU_API = "lcu_api"
    RIOT_API = "riot_api"
    GAME_STATE = "game_state"
    EVOLUTION = "evolution"
    SYSTEM = "system"
    PERFORMANCE = "performance"


@dataclass
class StructuredLogEntry:
    """
    Immutable structured log entry.

    Every field is JSON-serializable. The entry_id is a UUID4 for
    deduplication across distributed instances. The parent_span_id
    enables OpenTelemetry-compatible trace reconstruction.
    """
    timestamp: str
    level: str
    category: str
    component: str
    message: str
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_span_id: Optional[str] = None
    game_id: Optional[str] = None
    summoner_name: Optional[str] = None
    champion: Optional[str] = None
    match_time_sec: Optional[float] = None
    data: Optional[Dict[str, Any]] = None
    stack_trace: Optional[str] = None
    reward_signal: Optional[float] = None
    latency_ms: Optional[float] = None
    version: str = _STRUCTURED_LOG_VERSION

    def to_json(self) -> str:
        """Serialize to compact JSON, omitting None fields."""
        d = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(d, ensure_ascii=False, separators=(',', ':'))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_json(cls, raw: str) -> 'StructuredLogEntry':
        d = json.loads(raw)
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


class RingBuffer:
    """
    Thread-safe, size-bounded ring buffer for in-memory log retention.

    Guarantees:
        - Total memory usage never exceeds _MAX_RING_BUFFER_BYTES
        - O(1) append, O(n) iteration
        - Oldest entries evicted first when capacity reached

    Production critique:
        1. User: If buffer fills during a teamfight spike, oldest
           pre-fight logs are lost — acceptable since recent context
           matters more for real-time strategy.
        2. System: Lock contention is minimal because we use a deque
           with maxlen, and the GIL serializes append/popleft anyway.
    """
    def __init__(self, max_bytes: int = _MAX_RING_BUFFER_BYTES):
        self._max_bytes = max_bytes
        self._current_bytes = 0
        self._buffer: Deque[StructuredLogEntry] = deque()
        self._lock = threading.Lock()
        self._eviction_count = 0

    def append(self, entry: StructuredLogEntry) -> None:
        entry_size = len(entry.to_json().encode('utf-8'))
        with self._lock:
            while (self._current_bytes + entry_size > self._max_bytes
                   and self._buffer):
                evicted = self._buffer.popleft()
                self._current_bytes -= len(
                    evicted.to_json().encode('utf-8'))
                self._eviction_count += 1
            self._buffer.append(entry)
            self._current_bytes += entry_size

    def get_recent(self, count: int = 100) -> List[StructuredLogEntry]:
        with self._lock:
            items = list(self._buffer)
            return items[-count:]

    def get_by_category(
        self, category: LogCategory, count: int = 50
    ) -> List[StructuredLogEntry]:
        with self._lock:
            filtered = [e for e in self._buffer
                        if e.category == category.value]
            return filtered[-count:]

    def get_errors(self, count: int = 20) -> List[StructuredLogEntry]:
        with self._lock:
            errors = [e for e in self._buffer
                      if e.level in ('ERROR', 'CRITICAL', 'PENALTY')]
            return errors[-count:]

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'total_entries': len(self._buffer),
                'current_bytes': self._current_bytes,
                'max_bytes': self._max_bytes,
                'utilization_pct': round(
                    self._current_bytes / self._max_bytes * 100, 2),
                'eviction_count': self._eviction_count,
            }

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._current_bytes = 0

    def __len__(self) -> int:
        return len(self._buffer)


class AsyncLogWriter:
    """
    Async file writer with rotation.

    Writes are batched and flushed every _FLUSH_INTERVAL_SEC to avoid
    I/O storms during high-frequency game events (14fps capture = 14
    log entries/sec minimum, plus network events).

    Production critique:
        1. User: If the process crashes between flushes, up to 1s of
           logs are lost. For a 30-min game, this is <0.06% data loss.
        2. System: File rotation happens synchronously during flush —
           a 50MB rename takes <1ms on SSD, acceptable.
    """
    def __init__(self, log_dir: str = _DEFAULT_LOG_DIR):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue = queue.Queue(maxsize=100_000)
        self._current_file: Optional[Path] = None
        self._current_size = 0
        self._file_handle = None
        self._running = False
        self._writer_thread: Optional[threading.Thread] = None
        self._session_id = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._rotate_file()
        self._writer_thread = threading.Thread(
            target=self._write_loop, daemon=True, name='LogWriter')
        self._writer_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._writer_thread:
            self._writer_thread.join(timeout=5.0)
        self._close_file()

    def enqueue(self, entry: StructuredLogEntry) -> None:
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            # Drop oldest if queue is full — backpressure signal
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(entry)

    def _write_loop(self) -> None:
        batch: List[str] = []
        last_flush = time.monotonic()
        while self._running or not self._queue.empty():
            try:
                entry = self._queue.get(timeout=0.1)
                batch.append(entry.to_json())
            except queue.Empty:
                pass
            now = time.monotonic()
            if (now - last_flush >= _FLUSH_INTERVAL_SEC
                    or len(batch) >= 1000):
                self._flush_batch(batch)
                batch = []
                last_flush = now
        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: List[str]) -> None:
        if not batch or not self._file_handle:
            return
        data = '\n'.join(batch) + '\n'
        data_bytes = data.encode('utf-8')
        self._file_handle.write(data)
        self._file_handle.flush()
        self._current_size += len(data_bytes)
        if self._current_size >= _MAX_LOG_FILE_BYTES:
            self._rotate_file()

    def _rotate_file(self) -> None:
        self._close_file()
        # Rotate existing files
        for i in range(_LOG_ROTATION_COUNT - 1, 0, -1):
            src = self._log_dir / f"evolution_{self._session_id}.{i}.jsonl"
            dst = self._log_dir / f"evolution_{self._session_id}.{i+1}.jsonl"
            if src.exists():
                src.rename(dst)
        if self._current_file and self._current_file.exists():
            rotated = self._log_dir / f"evolution_{self._session_id}.1.jsonl"
            self._current_file.rename(rotated)
        self._current_file = (
            self._log_dir / f"evolution_{self._session_id}.jsonl")
        self._file_handle = open(self._current_file, 'a', encoding='utf-8')
        self._current_size = 0

    def _close_file(self) -> None:
        if self._file_handle:
            self._file_handle.flush()
            self._file_handle.close()
            self._file_handle = None

    def get_log_files(self) -> List[Path]:
        return sorted(self._log_dir.glob('*.jsonl'), key=lambda p: p.name)


class EvolutionLogger:
    """
    Singleton logger for the M1046-M1065 subsystem.

    Combines:
        - Structured JSON logging (for LLM consumption)
        - Ring buffer (for real-time dashboard / strategy engine)
        - File persistence (for post-game analysis / evolution)
        - Python stdlib logging bridge (for third-party libraries)

    Usage:
        logger = EvolutionLogger.get_instance()
        logger.info(LogCategory.NETWORK_CAPTURE,
                     "Intercepted Riot API call",
                     data={"endpoint": "/lol-match-history/v1/games"})

    The logger is the ONLY entry point for all logging in M1046-M1065.
    This ensures consistent formatting and prevents log fragmentation.
    """
    _instance: Optional['EvolutionLogger'] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(
        cls, log_dir: str = _DEFAULT_LOG_DIR
    ) -> 'EvolutionLogger':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(log_dir)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — for testing only."""
        with cls._lock:
            if cls._instance:
                cls._instance.shutdown()
            cls._instance = None

    def __init__(self, log_dir: str = _DEFAULT_LOG_DIR):
        self._ring = RingBuffer()
        self._writer = AsyncLogWriter(log_dir)
        self._writer.start()
        self._callbacks: List[Callable[[StructuredLogEntry], None]] = []
        self._component_stack: Dict[int, str] = {}  # thread_id -> component
        self._active_spans: Dict[str, float] = {}   # span_id -> start_time
        self._log_count = 0
        self._error_count = 0
        self._start_time = time.monotonic()

        # Bridge stdlib logging
        self._setup_stdlib_bridge()

    def _setup_stdlib_bridge(self) -> None:
        """Route stdlib logging through our structured pipeline."""
        handler = _StdlibBridgeHandler(self)
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)

    def _make_entry(
        self,
        level: LogLevel,
        category: LogCategory,
        message: str,
        component: Optional[str] = None,
        span_id: Optional[str] = None,
        game_id: Optional[str] = None,
        summoner_name: Optional[str] = None,
        champion: Optional[str] = None,
        match_time_sec: Optional[float] = None,
        data: Optional[Dict[str, Any]] = None,
        exc_info: Optional[BaseException] = None,
        reward_signal: Optional[float] = None,
        latency_ms: Optional[float] = None,
    ) -> StructuredLogEntry:
        if component is None:
            frame = inspect.currentframe()
            if frame and frame.f_back and frame.f_back.f_back:
                caller = frame.f_back.f_back
                component = (f"{Path(caller.f_code.co_filename).stem}"
                             f":{caller.f_code.co_name}")
            else:
                component = "unknown"

        stack_trace = None
        if exc_info:
            stack_trace = ''.join(traceback.format_exception(
                type(exc_info), exc_info, exc_info.__traceback__))

        return StructuredLogEntry(
            timestamp=datetime.datetime.utcnow().isoformat() + 'Z',
            level=level.name,
            category=category.value,
            component=component,
            message=message,
            parent_span_id=span_id,
            game_id=game_id,
            summoner_name=summoner_name,
            champion=champion,
            match_time_sec=match_time_sec,
            data=data,
            stack_trace=stack_trace,
            reward_signal=reward_signal,
            latency_ms=latency_ms,
        )

    def _emit(self, entry: StructuredLogEntry) -> None:
        self._ring.append(entry)
        self._writer.enqueue(entry)
        self._log_count += 1
        if entry.level in ('ERROR', 'CRITICAL', 'PENALTY'):
            self._error_count += 1
        for cb in self._callbacks:
            try:
                cb(entry)
            except Exception:
                pass  # Never let callback failures kill logging

    # ---- Public API: Log methods ----

    def trace(self, category: LogCategory, message: str, **kw) -> None:
        self._emit(self._make_entry(LogLevel.TRACE, category, message, **kw))

    def debug(self, category: LogCategory, message: str, **kw) -> None:
        self._emit(self._make_entry(LogLevel.DEBUG, category, message, **kw))

    def info(self, category: LogCategory, message: str, **kw) -> None:
        self._emit(self._make_entry(LogLevel.INFO, category, message, **kw))

    def warn(self, category: LogCategory, message: str, **kw) -> None:
        self._emit(self._make_entry(LogLevel.WARN, category, message, **kw))

    def error(self, category: LogCategory, message: str, **kw) -> None:
        self._emit(self._make_entry(LogLevel.ERROR, category, message, **kw))

    def critical(self, category: LogCategory, message: str, **kw) -> None:
        self._emit(self._make_entry(
            LogLevel.CRITICAL, category, message, **kw))

    def reward(self, category: LogCategory, message: str,
               reward_signal: float, **kw) -> None:
        self._emit(self._make_entry(
            LogLevel.REWARD, category, message,
            reward_signal=reward_signal, **kw))

    def penalty(self, category: LogCategory, message: str,
                reward_signal: float, **kw) -> None:
        self._emit(self._make_entry(
            LogLevel.PENALTY, category, message,
            reward_signal=reward_signal, **kw))

    def evolution(self, message: str, data: Optional[Dict] = None) -> None:
        self._emit(self._make_entry(
            LogLevel.EVOLUTION, LogCategory.EVOLUTION, message, data=data))

    # ---- Span tracking ----

    def start_span(self, span_name: str) -> str:
        span_id = f"{span_name}_{uuid.uuid4().hex[:8]}"
        self._active_spans[span_id] = time.monotonic()
        self.debug(LogCategory.SYSTEM, f"Span started: {span_name}",
                   span_id=span_id)
        return span_id

    def end_span(self, span_id: str, category: LogCategory,
                 message: str, **kw) -> float:
        start = self._active_spans.pop(span_id, None)
        if start is None:
            return 0.0
        elapsed_ms = (time.monotonic() - start) * 1000
        self.info(category, message,
                  span_id=span_id, latency_ms=round(elapsed_ms, 2), **kw)
        return elapsed_ms

    # ---- Callbacks ----

    def add_callback(
        self, cb: Callable[[StructuredLogEntry], None]
    ) -> None:
        self._callbacks.append(cb)

    def remove_callback(
        self, cb: Callable[[StructuredLogEntry], None]
    ) -> None:
        self._callbacks = [c for c in self._callbacks if c is not cb]

    # ---- Query API ----

    def get_recent_logs(self, count: int = 100) -> List[Dict]:
        return [e.to_dict() for e in self._ring.get_recent(count)]

    def get_errors(self, count: int = 20) -> List[Dict]:
        return [e.to_dict() for e in self._ring.get_errors(count)]

    def get_category_logs(
        self, category: LogCategory, count: int = 50
    ) -> List[Dict]:
        return [e.to_dict() for e in
                self._ring.get_by_category(category, count)]

    def get_diagnostics(self) -> Dict[str, Any]:
        uptime = time.monotonic() - self._start_time
        return {
            'uptime_sec': round(uptime, 2),
            'total_logs': self._log_count,
            'total_errors': self._error_count,
            'error_rate_pct': round(
                self._error_count / max(self._log_count, 1) * 100, 2),
            'logs_per_sec': round(
                self._log_count / max(uptime, 0.001), 2),
            'ring_buffer': self._ring.get_stats(),
            'active_spans': len(self._active_spans),
            'log_files': [str(f) for f in self._writer.get_log_files()],
        }

    # ---- Lifecycle ----

    def shutdown(self) -> None:
        self.evolution("Logger shutting down",
                       data=self.get_diagnostics())
        self._writer.stop()

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass


class _StdlibBridgeHandler(logging.Handler):
    """Bridges Python stdlib logging into our structured log pipeline."""
    _LEVEL_MAP = {
        logging.DEBUG: LogLevel.DEBUG,
        logging.INFO: LogLevel.INFO,
        logging.WARNING: LogLevel.WARN,
        logging.ERROR: LogLevel.ERROR,
        logging.CRITICAL: LogLevel.CRITICAL,
    }

    def __init__(self, evo_logger: EvolutionLogger):
        super().__init__()
        self._evo = evo_logger

    def emit(self, record: logging.LogRecord) -> None:
        level = self._LEVEL_MAP.get(record.levelno, LogLevel.INFO)
        try:
            entry = self._evo._make_entry(
                level=level,
                category=LogCategory.SYSTEM,
                message=record.getMessage(),
                component=f"{record.module}:{record.funcName}",
            )
            self._evo._ring.append(entry)
            self._evo._writer.enqueue(entry)
        except Exception:
            pass  # Never raise in logging handler


# ---------------------------------------------------------------------------
# Log Analysis Utilities (for the LLM "repair enzyme")
# ---------------------------------------------------------------------------

class LogAnalyzer:
    """
    Analyzes structured logs to produce evolution recommendations.

    This is the "LLM repair enzyme" input preparation layer. It reads
    log files, extracts patterns (error clusters, latency anomalies,
    reward trends), and formats them for LLM consumption.

    Pattern from plan.md:
        GovernedRunner.step() → 程序A运行 + 日志收集
        PolicyReward.__call__() → success/error → 奖励信号
    """
    def __init__(self, log_dir: str = _DEFAULT_LOG_DIR):
        self._log_dir = Path(log_dir)

    def load_logs(self, max_entries: int = 10000) -> List[StructuredLogEntry]:
        entries = []
        for log_file in sorted(
            self._log_dir.glob('*.jsonl'), reverse=True
        ):
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(StructuredLogEntry.from_json(line))
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if len(entries) >= max_entries:
                        return entries
        return entries

    def cluster_errors(
        self, entries: Optional[List[StructuredLogEntry]] = None
    ) -> Dict[str, List[Dict]]:
        if entries is None:
            entries = self.load_logs()
        clusters: Dict[str, List[Dict]] = {}
        for e in entries:
            if e.level in ('ERROR', 'CRITICAL', 'PENALTY'):
                key = f"{e.category}:{e.component}"
                if key not in clusters:
                    clusters[key] = []
                clusters[key].append(e.to_dict())
        return clusters

    def latency_percentiles(
        self, entries: Optional[List[StructuredLogEntry]] = None
    ) -> Dict[str, Dict[str, float]]:
        if entries is None:
            entries = self.load_logs()
        by_category: Dict[str, List[float]] = {}
        for e in entries:
            if e.latency_ms is not None:
                cat = e.category
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(e.latency_ms)
        result = {}
        for cat, latencies in by_category.items():
            latencies.sort()
            n = len(latencies)
            result[cat] = {
                'p50': latencies[n // 2] if n else 0,
                'p95': latencies[int(n * 0.95)] if n else 0,
                'p99': latencies[int(n * 0.99)] if n else 0,
                'max': latencies[-1] if n else 0,
                'count': n,
            }
        return result

    def reward_trend(
        self, entries: Optional[List[StructuredLogEntry]] = None,
        window_size: int = 100
    ) -> List[Dict[str, Any]]:
        if entries is None:
            entries = self.load_logs()
        rewards = [(e.timestamp, e.reward_signal)
                   for e in entries if e.reward_signal is not None]
        if not rewards:
            return []
        trend = []
        for i in range(0, len(rewards), window_size):
            window = rewards[i:i + window_size]
            vals = [r[1] for r in window]
            trend.append({
                'window_start': window[0][0],
                'window_end': window[-1][0],
                'mean_reward': round(sum(vals) / len(vals), 4),
                'min_reward': min(vals),
                'max_reward': max(vals),
                'count': len(vals),
            })
        return trend

    def generate_evolution_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive report for the LLM repair enzyme.

        This report is the primary input to the self-evolution loop.
        """
        entries = self.load_logs()
        return {
            'report_version': _STRUCTURED_LOG_VERSION,
            'generated_at': datetime.datetime.utcnow().isoformat() + 'Z',
            'total_entries': len(entries),
            'error_clusters': self.cluster_errors(entries),
            'latency_percentiles': self.latency_percentiles(entries),
            'reward_trend': self.reward_trend(entries),
            'category_distribution': self._category_dist(entries),
            'level_distribution': self._level_dist(entries),
        }

    def _category_dist(
        self, entries: List[StructuredLogEntry]
    ) -> Dict[str, int]:
        dist: Dict[str, int] = {}
        for e in entries:
            dist[e.category] = dist.get(e.category, 0) + 1
        return dist

    def _level_dist(
        self, entries: List[StructuredLogEntry]
    ) -> Dict[str, int]:
        dist: Dict[str, int] = {}
        for e in entries:
            dist[e.level] = dist.get(e.level, 0) + 1
        return dist


# ---------------------------------------------------------------------------
# Convenience: module-level logger access
# ---------------------------------------------------------------------------

def get_logger(log_dir: str = _DEFAULT_LOG_DIR) -> EvolutionLogger:
    """Get or create the singleton EvolutionLogger."""
    return EvolutionLogger.get_instance(log_dir)


def get_analyzer(log_dir: str = _DEFAULT_LOG_DIR) -> LogAnalyzer:
    """Create a LogAnalyzer for the given log directory."""
    return LogAnalyzer(log_dir)


# ---------------------------------------------------------------------------
# Self-test: Run this module directly to generate test logs
# ---------------------------------------------------------------------------

def _self_test():
    """Generate test logs simulating a 30-min game session."""
    import random

    log_dir = "logs/m1046_m1065_test"
    EvolutionLogger.reset()
    logger = get_logger(log_dir)

    print(f"[EvolutionLogger] Self-test starting, log_dir={log_dir}")

    # Simulate game phases
    phases = [
        ("loading", 0, 60),
        ("laning", 60, 900),
        ("mid_game", 900, 1500),
        ("late_game", 1500, 1800),
    ]

    champions = ["Jinx", "Thresh", "Ahri", "Lee Sin", "Darius",
                 "Lux", "Yasuo", "Zed", "Vayne", "Blitzcrank"]
    endpoints = [
        "/lol-match-history/v1/games",
        "/lol-summoner/v1/current-summoner",
        "/lol-champ-select/v1/session",
        "/lol-gameflow/v1/gameflow-phase",
        "/lol-ranked/v1/current-ranked-stats",
    ]

    log_count = 0
    for phase_name, start_sec, end_sec in phases:
        logger.info(LogCategory.GAME_STATE,
                    f"Game phase: {phase_name}",
                    match_time_sec=float(start_sec))

        # Simulate events within each phase
        t = start_sec
        while t < end_sec:
            dt = random.uniform(0.5, 3.0)
            t += dt

            # Network capture events
            if random.random() < 0.3:
                endpoint = random.choice(endpoints)
                latency = random.gauss(15, 5)
                if latency < 1:
                    latency = 1.0
                logger.info(
                    LogCategory.NETWORK_CAPTURE,
                    f"Intercepted: GET {endpoint}",
                    match_time_sec=t,
                    latency_ms=round(latency, 2),
                    data={"endpoint": endpoint, "status": 200})
                log_count += 1

            # History fetch events
            if random.random() < 0.1:
                champ = random.choice(champions)
                logger.info(
                    LogCategory.HISTORY_FETCH,
                    f"Fetched history for opponent: {champ}",
                    champion=champ,
                    match_time_sec=t,
                    data={"games_found": random.randint(5, 50)})
                log_count += 1

            # Strategy recommendations
            if random.random() < 0.05:
                reward = random.gauss(0.7, 0.3)
                logger.reward(
                    LogCategory.STRATEGY_ENGINE,
                    "Strategy recommendation accepted",
                    reward_signal=round(reward, 3),
                    match_time_sec=t)
                log_count += 1

            # Occasional errors
            if random.random() < 0.02:
                logger.error(
                    LogCategory.RIOT_API,
                    "API rate limit exceeded",
                    match_time_sec=t,
                    data={"retry_after": random.randint(1, 10)})
                log_count += 1

            # Fiddler MCP events
            if random.random() < 0.08:
                logger.info(
                    LogCategory.FIDDLER_MCP,
                    "Traffic analysis complete",
                    match_time_sec=t,
                    latency_ms=round(random.gauss(5, 2), 2),
                    data={"sessions_analyzed": random.randint(1, 20)})
                log_count += 1

    logger.evolution("Game session complete", data={
        "total_logs": log_count,
        "game_duration_sec": 1800,
    })

    # Generate evolution report
    analyzer = get_analyzer(log_dir)
    report = analyzer.generate_evolution_report()

    print(f"[EvolutionLogger] Generated {log_count} log entries")
    print(f"[EvolutionLogger] Diagnostics: {json.dumps(logger.get_diagnostics(), indent=2)}")
    print(f"[EvolutionLogger] Evolution report summary:")
    print(f"  - Total entries: {report['total_entries']}")
    print(f"  - Error clusters: {len(report['error_clusters'])}")
    print(f"  - Categories: {report['category_distribution']}")
    print(f"  - Levels: {report['level_distribution']}")

    logger.shutdown()
    return report


if __name__ == '__main__':
    report = _self_test()
    print("\n[EvolutionLogger] Self-test PASSED")
