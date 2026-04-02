"""
ChannelMonitor — Real-time channel health diagnostics.
========================================================
lolbot-HyperAI · Cyber Framework

Monitors all CyberNode channels for health: message rates, staleness,
backpressure drops, and subscriber counts.  Detects "dead channels"
(no messages >5s) and "avalanche channels" (>100 msg/s).

Architecture position:
    cyber/diagnostics/channel_monitor.py   ← YOU ARE HERE
    ├─ Reads: cyber/node/node._GLOBAL_CHANNELS (internal registry)
    ├─ Publishes: /lol/channel_health (ChannelHealthReport)
    └─ Consumed by: DreamView dashboard, CLI monitor, health_monitor

Apollo reference:
    cyber/tools/cyber_monitor/ — real-time topic diagnostics

Design notes:
    - Non-invasive: reads _write_count and subscriber state directly
    - Samples at 1Hz, computes rolling rates over 10s windows
    - Dead channel threshold: configurable (default 5s)
    - Avalanche threshold: configurable (default 100 msg/s)
    - Thread-safe: accesses _GLOBAL_CHANNELS under its lock
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("cyber.diagnostics")

_SAMPLE_INTERVAL_S = 1.0
_RATE_WINDOW_S = 10.0
_DEAD_THRESHOLD_S = 5.0
_AVALANCHE_THRESHOLD_MSG_PER_S = 100.0
_HISTORY_SIZE = 60  # 60 samples = 1 minute of history


@dataclass
class ChannelSnapshot:
    """Point-in-time snapshot of a single channel."""
    name: str
    write_count: int = 0
    subscriber_count: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ChannelStats:
    """Computed statistics for a single channel."""
    name: str
    rate_msg_per_s: float = 0.0
    total_messages: int = 0
    subscriber_count: int = 0
    last_message_age_s: float = 0.0
    is_dead: bool = False
    is_avalanche: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "rate_msg_s": round(self.rate_msg_per_s, 2),
            "total": self.total_messages,
            "subscribers": self.subscriber_count,
            "last_age_s": round(self.last_message_age_s, 1),
            "dead": self.is_dead,
            "avalanche": self.is_avalanche,
        }


@dataclass
class ChannelHealthReport:
    """Consolidated health report for all channels."""
    channels: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dead_channels: List[str] = field(default_factory=list)
    avalanche_channels: List[str] = field(default_factory=list)
    total_channels: int = 0
    total_rate_msg_per_s: float = 0.0
    timestamp: float = field(default_factory=time.time)


class ChannelMonitor:
    """Monitors CyberNode channel health.

    Usage::
        monitor = ChannelMonitor()
        monitor.start()          # begins background sampling
        report = monitor.report()  # get latest health report
        monitor.stop()

    Or in single-shot mode::
        report = monitor.sample_once()
    """

    def __init__(
        self,
        dead_threshold_s: float = _DEAD_THRESHOLD_S,
        avalanche_threshold: float = _AVALANCHE_THRESHOLD_MSG_PER_S,
    ) -> None:
        self._dead_threshold = dead_threshold_s
        self._avalanche_threshold = avalanche_threshold

        # Per-channel sample history: channel_name -> deque of (timestamp, write_count)
        self._history: Dict[str, Deque[Tuple[float, int]]] = {}
        self._last_report: Optional[ChannelHealthReport] = None
        self._lock = threading.Lock()

        # Background thread
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start background sampling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sample_loop,
            name="channel-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("ChannelMonitor started (sample=%.0fs, dead=%.0fs)",
                     _SAMPLE_INTERVAL_S, self._dead_threshold)

    def stop(self) -> None:
        """Stop background sampling."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("ChannelMonitor stopped")

    def report(self) -> Optional[ChannelHealthReport]:
        """Return the latest health report."""
        with self._lock:
            return self._last_report

    def sample_once(self) -> ChannelHealthReport:
        """Take a single sample and return a health report."""
        self._take_sample()
        return self._compute_report()

    # ─── Internal ────────────────────────────────────────────────────

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            self._take_sample()
            report = self._compute_report()
            with self._lock:
                self._last_report = report

            # Log warnings
            if report.dead_channels:
                logger.warning("Dead channels: %s",
                               ", ".join(report.dead_channels))
            if report.avalanche_channels:
                logger.warning("Avalanche channels: %s",
                               ", ".join(report.avalanche_channels))

            self._stop_event.wait(timeout=_SAMPLE_INTERVAL_S)

    def _take_sample(self) -> None:
        """Sample write counts from all global channels."""
        from cyber.node.node import _GLOBAL_CHANNELS, _CHANNEL_REGISTRY_LOCK

        now = time.time()
        snapshots: List[ChannelSnapshot] = []

        with _CHANNEL_REGISTRY_LOCK:
            for name, channel in _GLOBAL_CHANNELS.items():
                snap = ChannelSnapshot(
                    name=name,
                    write_count=channel._write_count,
                    subscriber_count=len(channel._subscribers),
                    timestamp=now,
                )
                snapshots.append(snap)

        # Store in history
        for snap in snapshots:
            if snap.name not in self._history:
                self._history[snap.name] = deque(maxlen=_HISTORY_SIZE)
            self._history[snap.name].append((snap.timestamp, snap.write_count))

    def _compute_report(self) -> ChannelHealthReport:
        """Compute health report from sample history."""
        now = time.time()
        channels: Dict[str, Dict[str, Any]] = {}
        dead: List[str] = []
        avalanche: List[str] = []
        total_rate = 0.0

        for name, samples in self._history.items():
            if len(samples) < 2:
                stat = ChannelStats(name=name, total_messages=samples[-1][1] if samples else 0)
                channels[name] = stat.to_dict()
                continue

            # Compute rate over window
            newest_ts, newest_count = samples[-1]
            oldest_ts, oldest_count = samples[0]

            # Find sample closest to RATE_WINDOW_S ago
            window_start = now - _RATE_WINDOW_S
            for ts, count in samples:
                if ts >= window_start:
                    oldest_ts, oldest_count = ts, count
                    break

            dt = newest_ts - oldest_ts
            if dt > 0:
                rate = (newest_count - oldest_count) / dt
            else:
                rate = 0.0

            # Staleness: time since last write count increment
            last_increment_age = 0.0
            for i in range(len(samples) - 1, 0, -1):
                if samples[i][1] != samples[i - 1][1]:
                    last_increment_age = now - samples[i][0]
                    break
            else:
                # Never seen an increment
                last_increment_age = now - samples[0][0] if samples else 999.0

            is_dead = last_increment_age > self._dead_threshold and newest_count > 0
            is_avalanche = rate > self._avalanche_threshold

            # Get subscriber count from latest snapshot
            sub_count = 0
            try:
                from cyber.node.node import _GLOBAL_CHANNELS, _CHANNEL_REGISTRY_LOCK
                with _CHANNEL_REGISTRY_LOCK:
                    ch = _GLOBAL_CHANNELS.get(name)
                    if ch:
                        sub_count = len(ch._subscribers)
            except Exception:
                pass

            stat = ChannelStats(
                name=name,
                rate_msg_per_s=max(0.0, rate),
                total_messages=newest_count,
                subscriber_count=sub_count,
                last_message_age_s=last_increment_age,
                is_dead=is_dead,
                is_avalanche=is_avalanche,
            )
            channels[name] = stat.to_dict()
            total_rate += max(0.0, rate)

            if is_dead:
                dead.append(name)
            if is_avalanche:
                avalanche.append(name)

        return ChannelHealthReport(
            channels=channels,
            dead_channels=sorted(dead),
            avalanche_channels=sorted(avalanche),
            total_channels=len(channels),
            total_rate_msg_per_s=round(total_rate, 2),
        )
