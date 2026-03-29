"""
Fiddler Dashboard Data — Dashboard metrics aggregation for Fiddler bridge.
Location: extensions/fiddler-bridge/src/fiddler_dashboard_data.py
Reference: Akagi monitoring, Fiddler Everywhere dashboard data
"""
from __future__ import annotations
import logging, time
from collections import Counter, defaultdict
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_dashboard_data.v1"

class FiddlerDashboardData:
    def __init__(self) -> None:
        self._packets: list[dict[str, Any]] = []
        self._sessions: set[str] = set()
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_packet(self, packet: dict[str, Any]) -> None:
        self._packets.append({**packet, "_recorded_at": time.time()})
        if self.evolution_callback:
            self.evolution_callback({"type": "packet_recorded", "key": _EVOLUTION_KEY, "timestamp": time.time()})

    def start_session(self, session_id: str) -> None:
        self._sessions.add(session_id)

    def end_session(self, session_id: str) -> None:
        self._sessions.discard(session_id)

    def get_dashboard(self) -> dict[str, Any]:
        n = len(self._packets)
        if n == 0:
            return {"total_packets": 0, "active_sessions": len(self._sessions),
                    "total_bytes": 0, "error_rate": 0.0, "avg_latency_ms": 0.0,
                    "top_endpoints": []}

        total_bytes = sum(p.get("size_bytes", 0) for p in self._packets)
        errors = sum(1 for p in self._packets if p.get("status", 200) >= 400)
        latencies = [p.get("latency_ms", 0) for p in self._packets]

        # Top endpoints
        url_counts = Counter(p.get("url", "") for p in self._packets)
        top = [{"url": url, "count": cnt} for url, cnt in url_counts.most_common(10)]

        return {
            "total_packets": n,
            "active_sessions": len(self._sessions),
            "total_bytes": total_bytes,
            "error_rate": errors / n if n > 0 else 0.0,
            "avg_latency_ms": sum(latencies) / n if n > 0 else 0.0,
            "top_endpoints": top,
        }

    def get_time_series(self, window_seconds: int = 60) -> list[dict[str, Any]]:
        now = time.time()
        relevant = [p for p in self._packets if now - p.get("_recorded_at", 0) < window_seconds]
        # Group into 1-second buckets
        buckets: dict[int, list] = defaultdict(list)
        for p in relevant:
            bucket = int(p.get("_recorded_at", 0))
            buckets[bucket].append(p)
        return [{"timestamp": ts, "count": len(pkts),
                 "avg_latency": sum(p.get("latency_ms", 0) for p in pkts) / len(pkts)}
                for ts, pkts in sorted(buckets.items())]
