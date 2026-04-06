#!/usr/bin/env python3
"""
launch/structured_logger.py — Structured Runtime Log Collector
================================================================
lolbot-HyperAI · Launch Layer

查看 Apollo cyber/logger/ 上现有的日志系统实现方式, 理解其如何将
各模块的运行时指标统一收集并落盘。从 Apollo glog + cyber_logger
这个好例子开始。然后遵循该模式实现一个 StructuredLogger, 让
main_loop 可以自动收集所有组件的 ProcMetrics 和健康状态, 并能
以 JSONL 格式落盘供后续分析。

关键功能:
    - 定时从 ComponentRegistry 采集所有组件指标
    - JSONL 格式输出, 每行一个时间点的完整系统快照
    - 支持 gzip 压缩 rotate
    - 线程安全, 在独立 daemon 线程运行
    - 自动记录 session 边界 (game_start/game_end)

位置: lolbot-HyperAI/launch/structured_logger.py
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from modules.common.component_base import ComponentRegistry

logger = logging.getLogger(__name__)

_DEFAULT_COLLECT_INTERVAL_S = 10.0
_DEFAULT_LOG_DIR = "logs/metrics"
_MAX_LOG_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_LOG_FILES = 10


@dataclass
class MetricsSnapshot:
    """One point-in-time snapshot of all component metrics."""
    timestamp: float = field(default_factory=time.time)
    uptime_s: float = 0.0
    session_state: str = ""
    session_id: str = ""
    tick_count: int = 0
    error_count: int = 0
    components: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    system: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "ts": self.timestamp,
            "uptime_s": round(self.uptime_s, 1),
            "session_state": self.session_state,
            "session_id": self.session_id,
            "tick": self.tick_count,
            "errors": self.error_count,
            "components": self.components,
            "system": self.system,
        }, separators=(",", ":"), default=str)


class StructuredLogger:
    """Collects and persists structured runtime metrics from all components.

    Runs in a background thread, periodically querying the
    ComponentRegistry for health and performance data.

    Usage::

        slogger = StructuredLogger(log_dir="logs/metrics", interval_s=10.0)
        slogger.start()
        slogger.record_event("game_start", {"session_id": "abc"})
        stats = slogger.stop()
    """

    def __init__(
        self,
        log_dir: str = _DEFAULT_LOG_DIR,
        interval_s: float = _DEFAULT_COLLECT_INTERVAL_S,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self._log_dir = Path(log_dir)
        self._interval_s = interval_s
        self._stop_event = stop_event or threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._log_file: Optional[Any] = None
        self._log_path: Optional[Path] = None
        self._bytes_written: int = 0

        self._started = False
        self._start_time: float = 0.0
        self._snapshots_collected: int = 0
        self._events_recorded: int = 0
        self._rotations: int = 0

        self._state_providers: List[Callable[[], Dict[str, Any]]] = []

        self._session_state: str = ""
        self._session_id: str = ""
        self._tick_count: int = 0
        self._error_count: int = 0

    def start(self) -> None:
        if self._started:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._open_log_file()
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._started = True
        self._thread = threading.Thread(
            target=self._collection_loop, name="structured-logger", daemon=True,
        )
        self._thread.start()
        logger.info("StructuredLogger started: dir=%s, interval=%.1fs",
                     self._log_dir, self._interval_s)

    def stop(self) -> Dict[str, Any]:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._collect_and_write()
        self._close_log_file()
        self._started = False
        return self.stats()

    def set_session_state(self, state: str) -> None:
        self._session_state = state

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    def set_tick_count(self, count: int) -> None:
        self._tick_count = count

    def set_error_count(self, count: int) -> None:
        self._error_count = count

    def add_state_provider(self, provider: Callable[[], Dict[str, Any]]) -> None:
        self._state_providers.append(provider)

    def record_event(self, event_type: str,
                     details: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._events_recorded += 1
            entry = json.dumps({
                "ts": time.time(), "event": event_type,
                "details": details or {}, "session_id": self._session_id,
            }, separators=(",", ":"), default=str)
            self._write_line(entry)

    def _collection_loop(self) -> None:
        while not self._stop_event.is_set():
            self._collect_and_write()
            self._stop_event.wait(timeout=self._interval_s)

    def _collect_and_write(self) -> None:
        try:
            registry = ComponentRegistry.instance()
            snapshot = MetricsSnapshot(
                uptime_s=time.monotonic() - self._start_time,
                session_state=self._session_state,
                session_id=self._session_id,
                tick_count=self._tick_count,
                error_count=self._error_count,
            )
            snapshot.components = registry.health_summary()

            for provider in self._state_providers:
                try:
                    snapshot.system.update(provider())
                except Exception:
                    pass

            try:
                import resource
                usage = resource.getrusage(resource.RUSAGE_SELF)
                snapshot.system["rss_mb"] = round(usage.ru_maxrss / 1024, 1)
                snapshot.system["user_cpu_s"] = round(usage.ru_utime, 2)
                snapshot.system["sys_cpu_s"] = round(usage.ru_stime, 2)
            except (ImportError, AttributeError):
                pass

            with self._lock:
                self._write_line(snapshot.to_json())
                self._snapshots_collected += 1
                if self._bytes_written > _MAX_LOG_SIZE_BYTES:
                    self._rotate()

        except Exception as exc:
            logger.error("StructuredLogger collection error: %s", exc)

    def _open_log_file(self) -> None:
        ts = int(time.time())
        self._log_path = self._log_dir / f"metrics_{ts}.jsonl"
        self._log_file = open(self._log_path, "a", encoding="utf-8")
        self._bytes_written = 0

    def _close_log_file(self) -> None:
        if self._log_file:
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None

    def _write_line(self, line: str) -> None:
        if self._log_file:
            self._log_file.write(line + "\n")
            self._bytes_written += len(line) + 1
            if self._snapshots_collected % 10 == 0:
                self._log_file.flush()

    def _rotate(self) -> None:
        self._close_log_file()
        if self._log_path and self._log_path.exists():
            gz_path = self._log_path.with_suffix(".jsonl.gz")
            try:
                with open(self._log_path, "rb") as f_in:
                    with gzip.open(gz_path, "wb") as f_out:
                        f_out.writelines(f_in)
                self._log_path.unlink()
            except Exception as exc:
                logger.error("Log rotation failed: %s", exc)
        self._cleanup_old_logs()
        self._open_log_file()
        self._rotations += 1

    def _cleanup_old_logs(self) -> None:
        try:
            gz_files = sorted(
                self._log_dir.glob("metrics_*.jsonl.gz"),
                key=lambda p: p.stat().st_mtime,
            )
            while len(gz_files) > _MAX_LOG_FILES:
                gz_files.pop(0).unlink()
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        uptime = 0.0
        if self._start_time > 0:
            uptime = time.monotonic() - self._start_time
        return {
            "started": self._started,
            "log_dir": str(self._log_dir),
            "current_log": str(self._log_path) if self._log_path else "",
            "snapshots_collected": self._snapshots_collected,
            "events_recorded": self._events_recorded,
            "bytes_written": self._bytes_written,
            "rotations": self._rotations,
            "uptime_s": round(uptime, 1),
            "interval_s": self._interval_s,
        }
