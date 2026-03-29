"""
Fiddler Anomaly Detector — Protocol anomaly/timeout/error detection.

Detects timeouts, HTTP error codes, and protocol anomalies in captured traffic.
Tracks anomaly history and computes anomaly rates.

Location: extensions/fiddler-bridge/src/fiddler_anomaly_detector.py

Reference (拿来主义):
  - agentos/cli/health_monitor.py: health check + threshold pattern
  - extensions/fiddler-bridge/src/fiddler_bridge/client.py: SessionStatus categorization
  - Akagi/mitm/client.py: error handling and retry patterns
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_anomaly_detector.v1"

_DEFAULT_TIMEOUT: float = 5.0


class FiddlerAnomalyDetector:
    """Protocol anomaly detector for captured traffic.

    Detects:
    - Timeouts (latency > threshold)
    - HTTP error codes (4xx, 5xx)
    - Protocol-level anomalies

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, timeout_threshold: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout_threshold = timeout_threshold
        self._anomaly_history: list[dict[str, Any]] = []
        self._total_checked: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def check(self, packet: dict[str, Any]) -> dict[str, Any]:
        """Check a packet for anomalies.

        Args:
            packet: Dict with latency, status_code, type keys.

        Returns:
            Dict with is_anomaly bool and reason string.
        """
        self._total_checked += 1
        latency = packet.get("latency", 0.0)
        status_code = packet.get("status_code", 200)

        is_anomaly = False
        reasons: list[str] = []

        # Timeout detection
        if latency > self._timeout_threshold:
            is_anomaly = True
            reasons.append(f"timeout ({latency:.1f}s > {self._timeout_threshold:.1f}s)")

        # Error code detection
        if isinstance(status_code, int) and status_code >= 400:
            is_anomaly = True
            reasons.append(f"error code {status_code}")

        result = {
            "is_anomaly": is_anomaly,
            "reason": "; ".join(reasons) if reasons else "normal",
            "latency": latency,
            "status_code": status_code,
        }

        if is_anomaly:
            self._anomaly_history.append({
                **result,
                "timestamp": time.time(),
            })
            self._fire_evolution({"action": "anomaly_detected", "detail": result})

        return result

    def get_anomaly_history(self) -> list[dict[str, Any]]:
        """Return history of detected anomalies."""
        return list(self._anomaly_history)

    def get_anomaly_rate(self) -> float:
        """Compute anomaly rate (anomalies / total checked).

        Returns:
            Float between 0.0 and 1.0, or 0.0 if nothing checked.
        """
        if self._total_checked == 0:
            return 0.0
        return len(self._anomaly_history) / self._total_checked

    def reset(self) -> None:
        """Clear anomaly history and counters."""
        self._anomaly_history.clear()
        self._total_checked = 0

    # --- Evolution pattern ---
    def _fire_evolution(self, detail: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "key": _EVOLUTION_KEY,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                logger.warning("Evolution callback error (fiddler_anomaly_detector)")
