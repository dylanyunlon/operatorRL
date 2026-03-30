"""
Fiddler Anomaly Detector — protocol anomaly / latency spike detection.

Monitors captured HTTP traffic for anomalies: latency spikes, server
errors, empty responses, rate anomalies.  Maintains a bounded history
for dashboard display and alerting.

Location: extensions/fiddler_bridge/src/fiddler_anomaly_detector.py

Reference (拿来主义):
  - Akagi mitmproxy addon: error handling on broken responses
  - Seraphine connector.py: retry decorator counting exceptions
  - extensions/fiddler-bridge/src/fiddler_anomaly_detector.py: existing stub
  - extensions/protocol-decoder/src/protocol_health_monitor.py: health patterns
  - agentos/governance/telemetry_collector.py: metric aggregation

Design Notes (Knuth-level critique):
  User:
    - check() always returns a result dict — never throws.
    - Multiple anomaly types can fire for a single packet.
    - History is bounded — no OOM on long sessions.
  System:
    - Rate tracking uses a sliding time window, not a counter reset.
    - Anomaly detection is O(1) per check — no aggregation scans.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_anomaly_detector.v1"

_DEFAULT_LATENCY_THRESHOLD_MS: float = 200.0
_DEFAULT_HISTORY_SIZE: int = 100
_DEFAULT_RATE_WINDOW_SEC: float = 10.0
_DEFAULT_RATE_THRESHOLD: int = 100


class _AnomalyRecord:
    """Single anomaly event record."""

    __slots__ = ("ts", "url", "anomaly_types", "details")

    def __init__(
        self,
        url: str,
        anomaly_types: List[str],
        details: Dict[str, Any],
    ) -> None:
        self.ts = time.time()
        self.url = url
        self.anomaly_types = anomaly_types
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "url": self.url,
            "anomaly_types": self.anomaly_types,
            "details": self.details,
        }


class _RateTracker:
    """Sliding-window request rate tracker.

    Keeps timestamps of recent requests and counts how many fall
    within the current window.
    """

    __slots__ = ("_window_sec", "_timestamps", "_lock")

    def __init__(self, window_sec: float) -> None:
        self._window_sec = window_sec
        self._timestamps: Deque[float] = collections.deque()
        self._lock = threading.Lock()

    def record(self) -> int:
        """Record a new request and return current rate (count in window)."""
        now = time.time()
        cutoff = now - self._window_sec
        with self._lock:
            self._timestamps.append(now)
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)


class FiddlerAnomalyDetector:
    """Detect anomalies in Fiddler-captured HTTP traffic.

    Detects:
        - Latency spikes (response time > threshold)
        - Server errors (5xx status codes)
        - Empty body on data endpoints
        - Request rate anomalies (burst detection)

    Attributes:
        anomaly_count: Total anomalies detected.
        latency_threshold_ms: Threshold for latency spike detection.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        *,
        latency_threshold_ms: float = _DEFAULT_LATENCY_THRESHOLD_MS,
        history_size: int = _DEFAULT_HISTORY_SIZE,
        rate_window_sec: float = _DEFAULT_RATE_WINDOW_SEC,
        rate_threshold: int = _DEFAULT_RATE_THRESHOLD,
    ) -> None:
        self._latency_threshold_ms = latency_threshold_ms
        self._history_size = history_size
        self._rate_threshold = rate_threshold

        self._total_checked: int = 0
        self._anomaly_count: int = 0

        self._history: Deque[_AnomalyRecord] = collections.deque(maxlen=history_size)
        self._rate_tracker = _RateTracker(rate_window_sec)
        self._lock = threading.Lock()

        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def anomaly_count(self) -> int:
        return self._anomaly_count

    @property
    def latency_threshold_ms(self) -> float:
        return self._latency_threshold_ms

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """Check a single packet for anomalies.

        Args:
            packet: Dict with optional keys ``url``, ``latency_ms``,
                    ``status_code``, ``body``, ``headers``.

        Returns:
            Dict with ``is_anomaly`` bool and ``anomaly_types`` list.
        """
        self._total_checked += 1
        url = packet.get("url", "")
        anomaly_types: List[str] = []
        details: Dict[str, Any] = {}

        # 1. Latency spike
        latency = packet.get("latency_ms", 0)
        if isinstance(latency, (int, float)) and latency > self._latency_threshold_ms:
            anomaly_types.append("latency_spike")
            details["latency_ms"] = latency
            details["threshold_ms"] = self._latency_threshold_ms

        # 2. Server error
        status = packet.get("status_code", 200)
        if isinstance(status, int) and status >= 500:
            anomaly_types.append("server_error")
            details["status_code"] = status

        # 3. Client error
        if isinstance(status, int) and 400 <= status < 500:
            anomaly_types.append("client_error")
            details["status_code"] = status

        # 4. Empty body on data endpoint
        body = packet.get("body", None)
        if body is not None and isinstance(body, str) and len(body.strip()) == 0:
            if "liveclientdata" in url.lower() or "api" in url.lower():
                anomaly_types.append("empty_body")

        # 5. Rate anomaly
        current_rate = self._rate_tracker.record()
        if current_rate > self._rate_threshold:
            anomaly_types.append("rate_anomaly")
            details["current_rate"] = current_rate
            details["rate_threshold"] = self._rate_threshold

        is_anomaly = len(anomaly_types) > 0

        if is_anomaly:
            self._anomaly_count += 1
            record = _AnomalyRecord(url, anomaly_types, details)
            with self._lock:
                self._history.append(record)
            self._fire_evolution({
                "action": "anomaly_detected",
                "types": anomaly_types,
                "url": url,
            })

        return {
            "is_anomaly": is_anomaly,
            "anomaly_types": anomaly_types,
            "details": details,
            "url": url,
        }

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._history]

    def clear_history(self) -> int:
        with self._lock:
            n = len(self._history)
            self._history.clear()
            return n

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_checked": self._total_checked,
            "anomaly_count": self._anomaly_count,
            "anomaly_rate": (
                self._anomaly_count / self._total_checked
                if self._total_checked > 0
                else 0.0
            ),
            "history_size": len(self._history),
            "latency_threshold_ms": self._latency_threshold_ms,
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised in FiddlerAnomalyDetector")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"FiddlerAnomalyDetector(checked={self._total_checked}, "
            f"anomalies={self._anomaly_count})"
        )


default_detector: FiddlerAnomalyDetector = FiddlerAnomalyDetector()
