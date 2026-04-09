"""
launch/pipeline_diagnostics.py — Apollo-style Pipeline Flow Diagnostics.
=========================================================================
lolbot-HyperAI · Launch Layer

查看 Apollo ``cyber/tools/cyber_monitor`` 上现有的通道监控工具实现方式, 理解
其模式——特别是 **通道消息频率统计** 和 **端到端延迟追踪** 是如何分离的。从
Apollo ``cyber_monitor`` 的实时通道统计面板这个好例子开始。然后, 遵循该模式
实现一个新的 ``PipelineDiagnostics``, 让运维人员可以实时看到消息在
canbus → perception → prediction → planning 之间的流动频率和延迟, 并能发
现瓶颈（如某个组件 Proc() 耗时过长导致消息堆积）。接着引入 ``FlowTracer``
端到端追踪, 使开发者能够追踪一条原始 LCU 数据从采集到语音输出的完整路径,
同时优化采样率避免性能开销。随后整合到 Mainboard 的健康检查循环, 令系统支
持周期性自动打印流水线状态摘要, 进而增强运行时可观测性。最终完善异常检测
（消息丢失、延迟飙升、通道静默）, 确保诊断系统兼容 Apollo cyber_monitor
的设计理念, 全面系统性升级运维体验以达成 毫秒级流水线可见性 的目标。

Apollo reference:
    cyber/tools/cyber_monitor/screen.cc        — channel stats display
    cyber/tools/cyber_monitor/general_channel_message.cc  — msg counting

位置: lolbot-HyperAI/launch/pipeline_diagnostics.py
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("diagnostics")


# ─── Constants ───────────────────────────────────────────────────────────────

_WINDOW_SEC: float = 10.0          # frequency measurement window
_STALE_THRESHOLD_SEC: float = 5.0  # channel silent → stale
_LATENCY_WARN_MS: float = 500.0    # warn if E2E latency exceeds this
_MAX_SAMPLES: int = 200            # rolling samples per channel
_REPORT_INTERVAL_SEC: float = 10.0 # auto-report interval


# ─── Channel statistics ─────────────────────────────────────────────────────

@dataclass
class ChannelStats:
    """Per-channel message flow statistics.

    Tracks message count, frequency (Hz), and inter-message latency
    within a rolling window.
    """
    channel: str
    total_msgs: int = 0
    last_msg_time: float = 0.0
    _timestamps: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_MAX_SAMPLES)
    )
    _latencies_ms: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_MAX_SAMPLES)
    )

    def record(self, timestamp: float) -> None:
        """Record a message arrival."""
        if self.last_msg_time > 0:
            delta_ms = (timestamp - self.last_msg_time) * 1000.0
            self._latencies_ms.append(delta_ms)

        self.total_msgs += 1
        self.last_msg_time = timestamp
        self._timestamps.append(timestamp)

    @property
    def frequency_hz(self) -> float:
        """Compute message frequency over the rolling window."""
        if len(self._timestamps) < 2:
            return 0.0
        now = time.monotonic()
        cutoff = now - _WINDOW_SEC
        recent = [t for t in self._timestamps if t >= cutoff]
        if len(recent) < 2:
            return 0.0
        span = recent[-1] - recent[0]
        if span <= 0:
            return 0.0
        return (len(recent) - 1) / span

    @property
    def mean_interval_ms(self) -> float:
        if not self._latencies_ms:
            return 0.0
        return sum(self._latencies_ms) / len(self._latencies_ms)

    @property
    def max_interval_ms(self) -> float:
        if not self._latencies_ms:
            return 0.0
        return max(self._latencies_ms)

    @property
    def is_stale(self) -> bool:
        if self.last_msg_time == 0:
            return True
        return (time.monotonic() - self.last_msg_time) > _STALE_THRESHOLD_SEC

    def snapshot(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "total_msgs": self.total_msgs,
            "frequency_hz": round(self.frequency_hz, 2),
            "mean_interval_ms": round(self.mean_interval_ms, 1),
            "max_interval_ms": round(self.max_interval_ms, 1),
            "stale": self.is_stale,
            "last_msg_age_s": round(
                time.monotonic() - self.last_msg_time, 1
            ) if self.last_msg_time > 0 else -1,
        }


# ─── End-to-end flow trace ──────────────────────────────────────────────────

@dataclass
class FlowTrace:
    """Traces one message's journey through the pipeline.

    Apollo equivalent: end-to-end latency tracking in
    PredictionEndToEndProc (start_time → end_time per stage).
    """
    trace_id: str
    start_time: float = 0.0
    stages: List[Tuple[str, float]] = field(default_factory=list)

    def enter_stage(self, stage: str) -> None:
        self.stages.append((stage, time.monotonic()))

    @property
    def total_ms(self) -> float:
        if len(self.stages) < 2:
            return 0.0
        return (self.stages[-1][1] - self.stages[0][1]) * 1000.0

    def stage_latencies_ms(self) -> List[Tuple[str, float]]:
        result = []
        for i in range(1, len(self.stages)):
            name = self.stages[i][0]
            delta = (self.stages[i][1] - self.stages[i - 1][1]) * 1000.0
            result.append((name, delta))
        return result

    def summary(self) -> str:
        parts = []
        for name, lat_ms in self.stage_latencies_ms():
            parts.append(f"{name}={lat_ms:.1f}ms")
        total = self.total_ms
        return f"[{self.trace_id}] total={total:.1f}ms  {' → '.join(parts)}"


# ─── Pipeline Diagnostics ───────────────────────────────────────────────────

class PipelineDiagnostics:
    """Aggregates channel stats and flow traces for the whole pipeline.

    Apollo equivalent: cyber_monitor process — watches all channels and
    reports frequency, latency, stale status.

    Usage by Mainboard::

        diag = PipelineDiagnostics()
        diag.register_channels([
            "/lol/raw_lcu", "/lol/game_state",
            "/lol/win_prediction", "/lol/strategy",
        ])
        diag.start_auto_report(interval_sec=10.0)

        # In component Proc():
        diag.record_message("/lol/raw_lcu")

        # Periodic:
        print(diag.format_report())
    """

    # Expected pipeline flow order for E2E latency context
    PIPELINE_ORDER = [
        "/lol/raw_lcu",
        "/lol/game_state",
        "/lol/win_prediction",
        "/lol/strategy",
        "/lol/voice_command",
    ]

    def __init__(self) -> None:
        self._stats: Dict[str, ChannelStats] = {}
        self._lock = threading.Lock()

        # Auto-report
        self._report_thread: Optional[threading.Thread] = None
        self._report_stop = threading.Event()

        # Flow tracing (sampled)
        self._trace_sample_rate: int = 50  # trace every Nth message
        self._msg_counter: int = 0
        self._active_traces: Dict[str, FlowTrace] = {}
        self._completed_traces: Deque[FlowTrace] = deque(maxlen=100)

        # Anomaly callbacks
        self._anomaly_callbacks: List[Callable[[str, str], None]] = []

    # ── Channel registration ─────────────────────────────────────────────

    def register_channel(self, channel: str) -> None:
        """Register a channel for monitoring."""
        with self._lock:
            if channel not in self._stats:
                self._stats[channel] = ChannelStats(channel=channel)

    def register_channels(self, channels: List[str]) -> None:
        for ch in channels:
            self.register_channel(ch)

    # ── Message recording ────────────────────────────────────────────────

    def record_message(self, channel: str, trace_id: str = "") -> None:
        """Record that a message was published on a channel.

        Call this from component Proc() after publishing.
        """
        now = time.monotonic()
        with self._lock:
            stats = self._stats.get(channel)
            if stats is None:
                stats = ChannelStats(channel=channel)
                self._stats[channel] = stats
            stats.record(now)

        # Sampled flow tracing
        self._msg_counter += 1
        if trace_id and trace_id in self._active_traces:
            self._active_traces[trace_id].enter_stage(channel)
            # Complete trace when it reaches the last pipeline stage
            if channel in (self.PIPELINE_ORDER[-1], self.PIPELINE_ORDER[-2]):
                trace = self._active_traces.pop(trace_id, None)
                if trace:
                    self._completed_traces.append(trace)
                    if trace.total_ms > _LATENCY_WARN_MS:
                        self._fire_anomaly(
                            "high_e2e_latency",
                            f"E2E latency {trace.total_ms:.0f}ms > "
                            f"{_LATENCY_WARN_MS}ms: {trace.summary()}"
                        )

    def start_trace(self, trace_id: str, channel: str) -> None:
        """Start a new flow trace at the pipeline entry point."""
        trace = FlowTrace(trace_id=trace_id, start_time=time.monotonic())
        trace.enter_stage(channel)
        self._active_traces[trace_id] = trace

    # ── Anomaly detection ────────────────────────────────────────────────

    def on_anomaly(self, callback: Callable[[str, str], None]) -> None:
        """Register callback(anomaly_type, description)."""
        self._anomaly_callbacks.append(callback)

    def _fire_anomaly(self, atype: str, desc: str) -> None:
        logger.warning("Pipeline anomaly [%s]: %s", atype, desc)
        for cb in self._anomaly_callbacks:
            try:
                cb(atype, desc)
            except Exception:
                pass

    def check_anomalies(self) -> List[Tuple[str, str]]:
        """Check for stale channels and return list of (type, desc)."""
        anomalies = []
        with self._lock:
            for ch, stats in self._stats.items():
                if stats.total_msgs > 0 and stats.is_stale:
                    desc = (
                        f"Channel {ch} stale: last msg "
                        f"{time.monotonic() - stats.last_msg_time:.1f}s ago"
                    )
                    anomalies.append(("stale_channel", desc))
        return anomalies

    # ── Auto-report ──────────────────────────────────────────────────────

    def start_auto_report(
        self, interval_sec: float = _REPORT_INTERVAL_SEC
    ) -> None:
        """Start periodic diagnostic reporting in a background thread."""
        if self._report_thread is not None:
            return

        self._report_stop.clear()
        self._report_thread = threading.Thread(
            target=self._report_loop,
            args=(interval_sec,),
            name="pipeline-diagnostics",
            daemon=True,
        )
        self._report_thread.start()
        logger.info(
            "Pipeline diagnostics started (interval=%.0fs)", interval_sec
        )

    def stop_auto_report(self) -> None:
        self._report_stop.set()
        if self._report_thread:
            self._report_thread.join(timeout=3.0)
            self._report_thread = None

    def _report_loop(self, interval: float) -> None:
        while not self._report_stop.is_set():
            self._report_stop.wait(timeout=interval)
            if self._report_stop.is_set():
                break

            report = self.format_report()
            if report:
                # Print to stdout (not logger, to avoid JSON formatting)
                print(f"\n{report}\n", flush=True)

            # Check anomalies
            for atype, desc in self.check_anomalies():
                self._fire_anomaly(atype, desc)

    # ── Report formatting ────────────────────────────────────────────────

    def format_report(self) -> str:
        """Format a human-readable pipeline status report.

        Apollo equivalent: cyber_monitor screen refresh.
        """
        with self._lock:
            if not self._stats:
                return ""

            lines = ["┌─ Pipeline Flow Diagnostics " + "─" * 42 + "┐"]

            # Channel stats table
            lines.append(
                f"│ {'Channel':<28s} {'Hz':>6s} {'Mean':>8s} "
                f"{'Max':>8s} {'Total':>8s} {'Status':>8s} │"
            )
            lines.append("│" + "─" * 70 + "│")

            for ch in self.PIPELINE_ORDER:
                stats = self._stats.get(ch)
                if stats is None:
                    lines.append(
                        f"│ {ch:<28s} {'—':>6s} {'—':>8s} "
                        f"{'—':>8s} {'—':>8s} {'UNREG':>8s} │"
                    )
                    continue

                status = "STALE" if stats.is_stale else "OK"
                if stats.total_msgs == 0:
                    status = "WAIT"

                lines.append(
                    f"│ {ch:<28s} {stats.frequency_hz:>5.1f}Hz "
                    f"{stats.mean_interval_ms:>6.0f}ms "
                    f"{stats.max_interval_ms:>6.0f}ms "
                    f"{stats.total_msgs:>8d} "
                    f"{status:>8s} │"
                )

            # Extra channels not in pipeline order
            for ch, stats in sorted(self._stats.items()):
                if ch not in self.PIPELINE_ORDER:
                    status = "STALE" if stats.is_stale else "OK"
                    if stats.total_msgs == 0:
                        status = "WAIT"
                    lines.append(
                        f"│ {ch:<28s} {stats.frequency_hz:>5.1f}Hz "
                        f"{stats.mean_interval_ms:>6.0f}ms "
                        f"{stats.max_interval_ms:>6.0f}ms "
                        f"{stats.total_msgs:>8d} "
                        f"{status:>8s} │"
                    )

            # Recent E2E traces
            if self._completed_traces:
                lines.append("│" + "─" * 70 + "│")
                lines.append("│ Recent E2E traces:" + " " * 51 + "│")
                for trace in list(self._completed_traces)[-3:]:
                    summary = trace.summary()
                    if len(summary) > 68:
                        summary = summary[:65] + "..."
                    lines.append(f"│  {summary:<68s} │")

            lines.append("└" + "─" * 70 + "┘")
            return "\n".join(lines)

    # ── Snapshot for programmatic access ─────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot of all diagnostics."""
        with self._lock:
            channels = {
                ch: stats.snapshot()
                for ch, stats in self._stats.items()
            }
        return {
            "channels": channels,
            "total_messages": sum(
                s["total_msgs"] for s in channels.values()
            ),
            "stale_channels": [
                ch for ch, s in channels.items() if s["stale"]
            ],
            "completed_traces": len(self._completed_traces),
        }
