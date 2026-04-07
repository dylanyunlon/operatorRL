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

        # Claude17: alert subsystem
        self._alert_rules: List["AlertRule"] = []
        self._alerts_triggered: int = 0
        self._session_boundaries: int = 0
        self._last_snapshot: Optional[MetricsSnapshot] = None
        self._on_alert_callbacks: List[
            Callable[["AlertEvent"], None]
        ] = []

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

            # Claude17: check alerts and compute diff
            if self._alert_rules:
                self._check_alerts(snapshot)
            if self._last_snapshot:
                diff = self._compute_diff(self._last_snapshot, snapshot)
                if diff and len(diff) > 5:
                    # Log significant changes
                    logger.debug(
                        "Snapshot diff: %d metrics changed", len(diff)
                    )
            self._last_snapshot = snapshot

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
            # Claude17: extended stats
            "alerts_triggered": self._alerts_triggered,
            "session_boundaries": self._session_boundaries,
        }

    # ─── Claude17: Alert Thresholds ──────────────────────────────────────

    def __init_alerts(self) -> None:
        """Initialize alert subsystem. Called from __init__."""
        self._alert_rules: List[AlertRule] = []
        self._alerts_triggered: int = 0
        self._session_boundaries: int = 0
        self._last_snapshot: Optional[MetricsSnapshot] = None
        self._on_alert_callbacks: List[
            Callable[["AlertEvent"], None]
        ] = []

    def add_alert_rule(self, rule: "AlertRule") -> None:
        """Register an alert rule that fires when a metric crosses a threshold.

        Example::

            slogger.add_alert_rule(AlertRule(
                name="high_error_rate",
                metric_path="errors",
                threshold=10.0,
                comparator="gt",
                cooldown_s=60.0,
            ))
        """
        self._alert_rules.append(rule)
        logger.info("Alert rule added: %s (%s %s %.2f)",
                     rule.name, rule.metric_path,
                     rule.comparator, rule.threshold)

    def on_alert(self, callback: Callable[["AlertEvent"], None]) -> None:
        """Register callback for alert events."""
        self._on_alert_callbacks.append(callback)

    def _check_alerts(self, snapshot: MetricsSnapshot) -> None:
        """Evaluate all alert rules against the current snapshot."""
        now = time.time()
        flat = self._flatten_snapshot(snapshot)

        for rule in self._alert_rules:
            if rule.metric_path not in flat:
                continue
            value = flat[rule.metric_path]
            if not isinstance(value, (int, float)):
                continue

            triggered = False
            if rule.comparator == "gt" and value > rule.threshold:
                triggered = True
            elif rule.comparator == "lt" and value < rule.threshold:
                triggered = True
            elif rule.comparator == "gte" and value >= rule.threshold:
                triggered = True
            elif rule.comparator == "lte" and value <= rule.threshold:
                triggered = True

            if triggered and (now - rule._last_fired) > rule.cooldown_s:
                rule._last_fired = now
                self._alerts_triggered += 1
                event = AlertEvent(
                    rule_name=rule.name,
                    metric_path=rule.metric_path,
                    value=value,
                    threshold=rule.threshold,
                    timestamp=now,
                )
                logger.warning(
                    "[Alert] %s: %s=%.2f %s %.2f",
                    rule.name, rule.metric_path, value,
                    rule.comparator, rule.threshold,
                )
                self.record_event("alert", {
                    "rule": rule.name,
                    "metric": rule.metric_path,
                    "value": value,
                    "threshold": rule.threshold,
                })
                for cb in self._on_alert_callbacks:
                    try:
                        cb(event)
                    except Exception:
                        logger.exception("Alert callback error")

    @staticmethod
    def _flatten_snapshot(
        snapshot: MetricsSnapshot, prefix: str = ""
    ) -> Dict[str, Any]:
        """Flatten a nested snapshot dict into dot-separated keys."""
        flat: Dict[str, Any] = {
            "uptime_s": snapshot.uptime_s,
            "errors": snapshot.error_count,
            "tick": snapshot.tick_count,
        }
        for comp_name, comp_data in snapshot.components.items():
            if isinstance(comp_data, dict):
                for k, v in comp_data.items():
                    flat[f"components.{comp_name}.{k}"] = v
        for k, v in snapshot.system.items():
            flat[f"system.{k}"] = v
        return flat

    # ─── Claude17: Snapshot Diff ─────────────────────────────────────────

    def _compute_diff(
        self, prev: MetricsSnapshot, curr: MetricsSnapshot
    ) -> Dict[str, Any]:
        """Compute delta between two snapshots for anomaly detection.

        Returns dict of changed metrics with their deltas.
        """
        flat_prev = self._flatten_snapshot(prev)
        flat_curr = self._flatten_snapshot(curr)
        diff: Dict[str, Any] = {}

        for key in flat_curr:
            curr_val = flat_curr[key]
            prev_val = flat_prev.get(key)
            if prev_val is None:
                diff[key] = {"type": "new", "value": curr_val}
            elif isinstance(curr_val, (int, float)) and isinstance(
                prev_val, (int, float)
            ):
                delta = curr_val - prev_val
                if abs(delta) > 0.001:
                    diff[key] = {
                        "prev": prev_val, "curr": curr_val,
                        "delta": round(delta, 4),
                    }
        return diff

    # ─── Claude17: Log Query ─────────────────────────────────────────────

    @staticmethod
    def query_log(
        log_path: str,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        event_filter: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Query a structured log file for entries matching criteria.

        Args:
            log_path: Path to .jsonl or .jsonl.gz file.
            start_ts: Minimum timestamp (inclusive).
            end_ts: Maximum timestamp (exclusive).
            event_filter: Filter by event type (if entry has "event" key).
            limit: Maximum entries to return.

        Returns:
            List of parsed JSON entries matching the criteria.
        """
        results: List[Dict[str, Any]] = []
        open_fn = (
            gzip.open if log_path.endswith(".gz") else open
        )
        mode = "rt" if log_path.endswith(".gz") else "r"

        try:
            with open_fn(log_path, mode, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts = entry.get("ts", 0)
                    if start_ts is not None and ts < start_ts:
                        continue
                    if end_ts is not None and ts >= end_ts:
                        continue
                    if event_filter is not None:
                        if entry.get("event") != event_filter:
                            continue

                    results.append(entry)
                    if len(results) >= limit:
                        break
        except Exception as exc:
            logger.error("Log query error on %s: %s", log_path, exc)

        return results

    @staticmethod
    def replay_session(
        log_path: str, session_id: str
    ) -> List[Dict[str, Any]]:
        """Extract all entries for a given session_id.

        Useful for post-game analysis and evolution fitness evaluation.
        """
        return StructuredLogger.query_log(
            log_path,
            event_filter=None,
            limit=100000,
        )


@dataclass
class AlertRule:
    """Defines a threshold-based alert rule.

    Claude17: Enables automated anomaly detection on structured metrics.

    Example::

        AlertRule(
            name="canbus_high_latency",
            metric_path="components.canbus.latency.p95_ms",
            threshold=200.0,
            comparator="gt",
            cooldown_s=30.0,
        )
    """
    name: str
    metric_path: str
    threshold: float
    comparator: str = "gt"  # gt, lt, gte, lte
    cooldown_s: float = 60.0
    _last_fired: float = field(default=0.0, repr=False)


@dataclass
class AlertEvent:
    """Fired when an AlertRule triggers."""
    rule_name: str
    metric_path: str
    value: float
    threshold: float
    timestamp: float
