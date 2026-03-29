"""
Protocol Health Monitor — Protocol decode health metrics + alerts.
Location: extensions/protocol-decoder/src/protocol_health_monitor.py
Reference: Akagi MITM health checks, DI-star worker health monitoring
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "extensions.protocol_decoder.protocol_health_monitor.v1"

class ProtocolHealthMonitor:
    def __init__(self, error_rate_threshold: float = 0.5) -> None:
        self._error_threshold = error_rate_threshold
        self._metrics: dict[str, dict[str, Any]] = defaultdict(lambda: {"success_count": 0, "error_count": 0, "latencies": [], "errors": []})
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_decode(self, protocol: str, success: bool, latency_ms: float, error: str = "") -> None:
        m = self._metrics[protocol]
        if success:
            m["success_count"] += 1
            m["latencies"].append(latency_ms)
        else:
            m["error_count"] += 1
            m["errors"].append(error)
        if self.evolution_callback:
            self.evolution_callback({"type": "decode_recorded", "key": _EVOLUTION_KEY,
                                     "protocol": protocol, "success": success, "timestamp": time.time()})

    def get_metrics(self, protocol: str) -> dict[str, Any]:
        m = self._metrics[protocol]
        return {"success_count": m["success_count"], "error_count": m["error_count"],
                "total": m["success_count"] + m["error_count"]}

    def is_healthy(self) -> bool:
        for proto, m in self._metrics.items():
            total = m["success_count"] + m["error_count"]
            if total > 0 and m["error_count"] / total > self._error_threshold:
                return False
        return True

    def latency_percentile(self, protocol: str, percentile: float) -> float:
        lats = sorted(self._metrics[protocol]["latencies"])
        if not lats:
            return 0.0
        idx = int(len(lats) * percentile / 100.0)
        idx = min(idx, len(lats) - 1)
        return lats[idx]

    def get_alerts(self) -> list[dict[str, Any]]:
        alerts = []
        for proto, m in self._metrics.items():
            total = m["success_count"] + m["error_count"]
            if total > 0:
                rate = m["error_count"] / total
                if rate > self._error_threshold:
                    alerts.append({"protocol": proto, "error_rate": rate, "severity": "critical"})
        return alerts

    def reset(self, protocol: str) -> None:
        self._metrics[protocol] = {"success_count": 0, "error_count": 0, "latencies": [], "errors": []}

    def global_summary(self) -> dict[str, Any]:
        total_d, total_e = 0, 0
        for m in self._metrics.values():
            total_d += m["success_count"] + m["error_count"]
            total_e += m["error_count"]
        return {"total_decodes": total_d, "total_errors": total_e,
                "protocols": list(self._metrics.keys())}
