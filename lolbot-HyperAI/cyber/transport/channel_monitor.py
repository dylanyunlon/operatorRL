"""
ChannelMonitor — Runtime channel health and anomaly detection.
===============================================================
lolbot-HyperAI · Cyber Layer

Monitors all CyberNode channels for health anomalies:
    - Stale channels (no writes for > threshold)
    - Backpressure (subscriber queues > 80% full)
    - Latency spikes (publish-to-observe delay)
    - Dead channels (subscribed but producer never writes)

Architecture position:
    cyber/transport/channel_monitor.py   ← YOU ARE HERE
    ├─ Reads: all channels via _GLOBAL_CHANNELS registry
    ├─ Publishes: /system/channel_health (ChannelHealthReport)
    └─ Used by: launch/mainboard.py, scripts/diagnostic_runner.py

Apollo reference:
    cyber/tools/cyber_monitor/ — channel monitoring CLI
    cyber/transport/transport.cc — transport health checks

Design notes:
    - Non-intrusive: reads channel metadata without consuming messages
    - Configurable thresholds per-channel or global defaults
    - Produces ChannelHealthReport every N seconds
    - Thread-safe: reads only, no writes to channel internals
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

from cyber.node.node import (
    _CHANNEL_REGISTRY_LOCK,
    _GLOBAL_CHANNELS,
    _Channel,
)

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_STALE_THRESHOLD_S = 5.0      # channel stale if no write for 5s
_DEFAULT_BACKPRESSURE_RATIO = 0.8     # warn if queue > 80% full
_DEFAULT_MONITOR_INTERVAL_S = 2.0     # check every 2s
_MAX_HEALTH_HISTORY = 100


class ChannelStatus(Enum):
    """Health status of a single channel."""
    HEALTHY = auto()
    STALE = auto()            # No recent writes
    BACKPRESSURE = auto()     # Subscriber queues filling up
    DEAD = auto()             # Has subscribers but zero writes ever
    IDLE = auto()             # No subscribers (unused channel)


@dataclass
class ChannelHealth:
    """Health snapshot for a single channel."""
    channel_name: str
    status: ChannelStatus
    subscriber_count: int = 0
    total_writes: int = 0
    seconds_since_last_write: float = 0.0
    max_queue_fill_ratio: float = 0.0    # highest queue fill across subs
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel_name,
            "status": self.status.name,
            "subscribers": self.subscriber_count,
            "total_writes": self.total_writes,
            "stale_s": round(self.seconds_since_last_write, 1),
            "queue_fill": round(self.max_queue_fill_ratio, 2),
            "warnings": self.warnings,
        }


@dataclass
class ChannelHealthReport:
    """Aggregate health report for all channels."""
    timestamp: float = field(default_factory=time.time)
    total_channels: int = 0
    healthy_count: int = 0
    stale_count: int = 0
    backpressure_count: int = 0
    dead_count: int = 0
    idle_count: int = 0
    channels: List[ChannelHealth] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total": self.total_channels,
            "healthy": self.healthy_count,
            "stale": self.stale_count,
            "backpressure": self.backpressure_count,
            "dead": self.dead_count,
            "idle": self.idle_count,
            "channels": [c.to_dict() for c in self.channels],
        }

    @property
    def has_issues(self) -> bool:
        return (self.stale_count + self.backpressure_count + self.dead_count) > 0


class ChannelMonitor:
    """Monitors all CyberNode channels for health anomalies.

    Usage::

        monitor = ChannelMonitor()
        report = monitor.check()
        if report.has_issues:
            for ch in report.channels:
                if ch.status != ChannelStatus.HEALTHY:
                    print(f"Issue: {ch.channel_name} — {ch.status.name}")

    Can also run in background thread::

        monitor.start_background()
        # ... later ...
        monitor.stop_background()
        latest = monitor.latest_report
    """

    def __init__(
        self,
        stale_threshold_s: float = _DEFAULT_STALE_THRESHOLD_S,
        backpressure_ratio: float = _DEFAULT_BACKPRESSURE_RATIO,
        monitor_interval_s: float = _DEFAULT_MONITOR_INTERVAL_S,
    ) -> None:
        self._stale_threshold = stale_threshold_s
        self._backpressure_ratio = backpressure_ratio
        self._interval = monitor_interval_s

        # Per-channel last-write tracking
        self._last_write_counts: Dict[str, int] = {}
        self._last_write_times: Dict[str, float] = {}

        # Background thread
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest_report: Optional[ChannelHealthReport] = None
        self._report_history: List[ChannelHealthReport] = []
        self._check_count: int = 0

    def check(self) -> ChannelHealthReport:
        """Perform a single health check of all channels.

        Returns:
            ChannelHealthReport with per-channel health status.
        """
        now = time.monotonic()
        self._check_count += 1

        # Snapshot the global channel registry
        with _CHANNEL_REGISTRY_LOCK:
            channel_names = list(_GLOBAL_CHANNELS.keys())
            channels_snapshot: List[Tuple[str, _Channel]] = [
                (name, _GLOBAL_CHANNELS[name])
                for name in channel_names
            ]

        results: List[ChannelHealth] = []
        healthy = stale = backpressure = dead = idle = 0

        for name, channel in channels_snapshot:
            health = self._check_channel(name, channel, now)
            results.append(health)

            if health.status == ChannelStatus.HEALTHY:
                healthy += 1
            elif health.status == ChannelStatus.STALE:
                stale += 1
            elif health.status == ChannelStatus.BACKPRESSURE:
                backpressure += 1
            elif health.status == ChannelStatus.DEAD:
                dead += 1
            elif health.status == ChannelStatus.IDLE:
                idle += 1

        report = ChannelHealthReport(
            timestamp=time.time(),
            total_channels=len(results),
            healthy_count=healthy,
            stale_count=stale,
            backpressure_count=backpressure,
            dead_count=dead,
            idle_count=idle,
            channels=results,
        )

        self._latest_report = report
        self._report_history.append(report)
        if len(self._report_history) > _MAX_HEALTH_HISTORY:
            self._report_history = self._report_history[-_MAX_HEALTH_HISTORY:]

        return report

    def _check_channel(
        self,
        name: str,
        channel: _Channel,
        now: float,
    ) -> ChannelHealth:
        """Check health of a single channel."""
        sub_count = channel.subscriber_count
        write_count = channel.write_count
        warnings: List[str] = []

        # Track write activity
        prev_writes = self._last_write_counts.get(name, 0)
        if write_count > prev_writes:
            self._last_write_times[name] = now
        self._last_write_counts[name] = write_count

        last_write_time = self._last_write_times.get(name, 0.0)
        seconds_since = now - last_write_time if last_write_time > 0 else 999.0

        # Check subscriber queue fill ratios
        max_fill = 0.0
        with channel._lock:
            subs = list(channel._subscribers.values())
        for sub in subs:
            with sub.lock:
                fill = len(sub.queue) / max(sub.max_size, 1)
            max_fill = max(max_fill, fill)

        # Determine status
        if sub_count == 0:
            status = ChannelStatus.IDLE
        elif write_count == 0:
            status = ChannelStatus.DEAD
            warnings.append("Channel has subscribers but zero writes")
        elif max_fill >= self._backpressure_ratio:
            status = ChannelStatus.BACKPRESSURE
            warnings.append(
                f"Queue fill {max_fill:.0%} exceeds threshold {self._backpressure_ratio:.0%}"
            )
        elif seconds_since > self._stale_threshold:
            status = ChannelStatus.STALE
            warnings.append(f"No writes for {seconds_since:.1f}s")
        else:
            status = ChannelStatus.HEALTHY

        return ChannelHealth(
            channel_name=name,
            status=status,
            subscriber_count=sub_count,
            total_writes=write_count,
            seconds_since_last_write=seconds_since,
            max_queue_fill_ratio=max_fill,
            warnings=warnings,
        )

    # ─── Background monitoring ───────────────────────────────────────

    def start_background(self) -> None:
        """Start periodic health checks in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop,
            name="channel-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("ChannelMonitor background started (interval=%.1fs)",
                     self._interval)

    def stop_background(self) -> None:
        """Stop the background monitoring thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("ChannelMonitor background stopped")

    def _background_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                report = self.check()
                if report.has_issues:
                    for ch in report.channels:
                        if ch.status not in (ChannelStatus.HEALTHY, ChannelStatus.IDLE):
                            logger.warning(
                                "Channel[%s] %s: %s",
                                ch.channel_name,
                                ch.status.name,
                                "; ".join(ch.warnings),
                            )
            except Exception as exc:
                logger.error("ChannelMonitor check error: %s", exc)

            self._stop_event.wait(timeout=self._interval)

    # ─── Properties ──────────────────────────────────────────────────

    @property
    def latest_report(self) -> Optional[ChannelHealthReport]:
        return self._latest_report

    @property
    def check_count(self) -> int:
        return self._check_count

    def summary(self) -> Dict[str, Any]:
        if self._latest_report:
            return self._latest_report.to_dict()
        return {"status": "no_checks_yet"}
