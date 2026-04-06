"""
cyber/diagnostics/channel_diagnostics.py — Channel health analysis.
=====================================================================
Claude18 · Extends Claude11's ChannelMonitor with root-cause diagnosis

Problem from diagnostic run:
    ChannelMonitor reports "3 backpressure, 1 dead" but gives no
    actionable information about WHICH channels or WHY.

Solution (Apollo monitor pattern):
    查看 Apollo modules/monitor/hardware_checker.cc 上现有硬件诊断的实现
    方式, 理解其模式, 特别是 per-device 状态如何聚合成 system report。从
    Apollo HardwareChecker 的 per-sensor check 这个好例子开始。然后, 遵循
    该模式实现一个新的 ChannelDiagnostics, 让 monitor 可以 按通道维度报告
    健康问题, 并能 给出修复建议。

File location: lolbot-HyperAI/cyber/diagnostics/channel_diagnostics.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from cyber.node.node import _GLOBAL_CHANNELS, _CHANNEL_REGISTRY_LOCK

logger = logging.getLogger(__name__)


class ChannelHealth(Enum):
    """Per-channel health classification."""
    HEALTHY = auto()       # Active writers and readers, data flowing
    IDLE = auto()          # Registered but no writes yet
    WRITE_ONLY = auto()    # Has writers but no subscribers (dead letter)
    READ_ONLY = auto()     # Has readers but nothing writes to it
    BACKPRESSURE = auto()  # Readers can't keep up, dropping messages
    STALE = auto()         # No new writes in > threshold


@dataclass
class ChannelReport:
    """Diagnostic report for a single channel."""
    channel_name: str
    health: ChannelHealth
    writer_count: int = 0
    subscriber_count: int = 0
    total_writes: int = 0
    max_pending: int = 0
    diagnosis: str = ""
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel_name,
            "health": self.health.name,
            "writers": self.writer_count,
            "subscribers": self.subscriber_count,
            "total_writes": self.total_writes,
            "max_pending": self.max_pending,
            "diagnosis": self.diagnosis,
            "recommendation": self.recommendation,
        }


@dataclass
class SystemChannelReport:
    """Aggregated report for all channels in the system."""
    timestamp: float = field(default_factory=time.time)
    channels: List[ChannelReport] = field(default_factory=list)
    healthy_count: int = 0
    warning_count: int = 0
    error_count: int = 0

    @property
    def has_issues(self) -> bool:
        return self.warning_count > 0 or self.error_count > 0

    def summary(self) -> str:
        lines = [
            f"Channel Diagnostics: {len(self.channels)} channels, "
            f"{self.healthy_count} healthy, "
            f"{self.warning_count} warnings, "
            f"{self.error_count} errors",
        ]
        for ch in self.channels:
            if ch.health not in (ChannelHealth.HEALTHY, ChannelHealth.IDLE):
                lines.append(
                    f"  [{ch.health.name}] {ch.channel_name}: "
                    f"{ch.diagnosis}"
                )
                if ch.recommendation:
                    lines.append(f"    → {ch.recommendation}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "healthy": self.healthy_count,
            "warnings": self.warning_count,
            "errors": self.error_count,
            "channels": [ch.to_dict() for ch in self.channels],
        }


class ChannelDiagnostics:
    """Per-channel health analysis with root-cause diagnosis.

    Unlike ChannelMonitor which only counts healthy/stale/dead,
    this reports PER CHANNEL what's wrong and suggests fixes.

    Usage::

        diag = ChannelDiagnostics()
        report = diag.analyze()
        if report.has_issues:
            print(report.summary())
    """

    # Channels that are expected to have no subscribers in testdata mode
    # (e.g. voice_queue has no TTS engine in test). Not an error.
    EXPECTED_WRITE_ONLY = {
        "/lol/voice_queue",
        "/lol/alert",
    }

    # Minimum writes before we consider a channel "active"
    MIN_ACTIVE_WRITES = 2

    def __init__(
        self,
        backpressure_threshold: float = 0.8,
        stale_threshold_s: float = 10.0,
    ) -> None:
        self._bp_threshold = backpressure_threshold
        self._stale_threshold_s = stale_threshold_s
        self._last_write_counts: Dict[str, int] = {}
        self._last_check_time: float = 0.0

    def analyze(self) -> SystemChannelReport:
        """Analyze all registered channels and produce a diagnostic report."""
        now = time.monotonic()
        report = SystemChannelReport(timestamp=time.time())

        with _CHANNEL_REGISTRY_LOCK:
            channel_names = list(_GLOBAL_CHANNELS.keys())

        for name in sorted(channel_names):
            ch_report = self._analyze_channel(name, now)
            report.channels.append(ch_report)

            if ch_report.health == ChannelHealth.HEALTHY:
                report.healthy_count += 1
            elif ch_report.health in (ChannelHealth.IDLE,):
                report.healthy_count += 1  # idle is normal at startup
            elif ch_report.health in (
                ChannelHealth.WRITE_ONLY, ChannelHealth.STALE,
            ):
                report.warning_count += 1
            else:
                report.error_count += 1

        self._last_check_time = now
        return report

    def _analyze_channel(
        self, name: str, now: float,
    ) -> ChannelReport:
        """Analyze a single channel."""
        with _CHANNEL_REGISTRY_LOCK:
            ch = _GLOBAL_CHANNELS.get(name)
            if ch is None:
                return ChannelReport(
                    channel_name=name,
                    health=ChannelHealth.IDLE,
                    diagnosis="Channel not found in registry",
                )

        sub_count = ch.subscriber_count
        write_count = ch.write_count

        # Check for pending queue pressure across all subscribers
        max_pending = 0
        max_capacity = 1
        with ch._lock:
            for sub in ch._subscribers.values():
                with sub.lock:
                    pending = len(sub.queue)
                    capacity = sub.max_size
                    if pending > max_pending:
                        max_pending = pending
                        max_capacity = capacity

        # Determine health
        prev_writes = self._last_write_counts.get(name, 0)
        self._last_write_counts[name] = write_count
        writes_since_last = write_count - prev_writes

        if sub_count == 0 and write_count > 0:
            # No subscribers but data is being written
            if name in self.EXPECTED_WRITE_ONLY:
                health = ChannelHealth.HEALTHY
                diagnosis = "Write-only (expected, no consumer needed in test)"
                recommendation = ""
            else:
                health = ChannelHealth.WRITE_ONLY
                diagnosis = (
                    f"No subscribers consuming {write_count} writes"
                )
                recommendation = (
                    f"Add a Reader for {name}, or remove the Writer "
                    f"to avoid wasted CPU"
                )
        elif write_count == 0:
            health = ChannelHealth.IDLE
            diagnosis = "No writes yet"
            recommendation = ""
        elif (
            max_capacity > 0
            and max_pending / max_capacity >= self._bp_threshold
        ):
            health = ChannelHealth.BACKPRESSURE
            diagnosis = (
                f"Queue {max_pending}/{max_capacity} "
                f"({max_pending/max_capacity*100:.0f}% full)"
            )
            recommendation = (
                f"Consumer on {name} is too slow. "
                f"Increase queue size or reduce producer rate."
            )
        elif (
            self._last_check_time > 0
            and writes_since_last == 0
            and now - self._last_check_time > self._stale_threshold_s
        ):
            health = ChannelHealth.STALE
            diagnosis = (
                f"No new writes in {now - self._last_check_time:.1f}s"
            )
            recommendation = "Check if the producing component is stuck"
        else:
            health = ChannelHealth.HEALTHY
            diagnosis = f"OK ({write_count} total writes, {sub_count} subs)"
            recommendation = ""

        return ChannelReport(
            channel_name=name,
            health=health,
            writer_count=1 if write_count > 0 else 0,
            subscriber_count=sub_count,
            total_writes=write_count,
            max_pending=max_pending,
            diagnosis=diagnosis,
            recommendation=recommendation,
        )
