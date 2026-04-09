"""
modules/monitor/alert/alert_manager.py
从 Apollo ``modules/monitor/`` 这个好例子开始——它将告警管理从组件中分离。
Extracted verbatim from MonitorComponent (Claude25 b0178fa).
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

_MAX_ALERTS = 200


class AlertSeverity(Enum):
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()


@dataclass
class AlertRecord:
    component: str
    category: str
    severity: AlertSeverity
    message: str
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False
    resolved_at: float = 0.0
    count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component,
            "category": self.category,
            "severity": self.severity.name,
            "message": self.message[:200],
            "timestamp": round(self.timestamp, 3),
            "resolved": self.resolved,
            "count": self.count,
        }


class AlertManager:
    """Manages alert lifecycle: fire, deduplicate, resolve, notify."""

    def __init__(self, max_alerts: int = _MAX_ALERTS) -> None:
        self._alerts: List[AlertRecord] = []
        self._max_alerts = max_alerts
        self._callbacks: List[Callable[[AlertRecord], None]] = []
        self._fire_count: int = 0
        self._resolve_count: int = 0

    def fire(
        self, component: str, category: str, severity: AlertSeverity,
        message: str, dedup_window_s: float = 30.0,
    ) -> AlertRecord:
        """Fire an alert. Deduplicates within window."""
        now = time.time()
        # Dedup: same component+category within window → increment count
        for alert in reversed(self._alerts):
            if (alert.component == component and alert.category == category
                    and not alert.resolved
                    and now - alert.timestamp < dedup_window_s):
                alert.count += 1
                alert.message = message
                alert.timestamp = now
                return alert

        record = AlertRecord(
            component=component, category=category,
            severity=severity, message=message, timestamp=now,
        )
        self._alerts.append(record)
        self._fire_count += 1

        # Trim
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]

        # Notify
        for cb in self._callbacks:
            try:
                cb(record)
            except Exception:
                pass

        return record

    def resolve(self, component: str, category: str) -> bool:
        """Resolve active alerts matching component+category."""
        resolved_any = False
        now = time.time()
        for alert in self._alerts:
            if (alert.component == component and alert.category == category
                    and not alert.resolved):
                alert.resolved = True
                alert.resolved_at = now
                self._resolve_count += 1
                resolved_any = True
        return resolved_any

    def active_alerts(self) -> List[AlertRecord]:
        return [a for a in self._alerts if not a.resolved]

    def recent(self, count: int = 20) -> List[AlertRecord]:
        return self._alerts[-count:]

    def on_alert(self, callback: Callable[[AlertRecord], None]) -> None:
        self._callbacks.append(callback)

    def stats(self) -> Dict[str, Any]:
        active = [a for a in self._alerts if not a.resolved]
        return {
            "total_fired": self._fire_count,
            "total_resolved": self._resolve_count,
            "active_count": len(active),
            "history_size": len(self._alerts),
        }
