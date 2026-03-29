"""
Fiddler Replay Engine — Captured traffic offline replay + timeline.

Records captured packets with timestamps for later replay.
Supports time-window filtering and JSON export.

Location: extensions/fiddler-bridge/src/fiddler_replay_engine.py

Reference (拿来主义):
  - extensions/fiddler-bridge/src/fiddler_bridge/session_capture.py: session recording
  - agentos/cli/session_recorder.py: recording + export pattern
  - Akagi/mitm/logger.py: packet logging with timestamps
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_replay_engine.v1"


class FiddlerReplayEngine:
    """Captured traffic replay engine.

    Records packets with timestamps, supports ordered replay,
    time-window filtering, and JSON export.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._packets: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def packet_count(self) -> int:
        return len(self._packets)

    def record(self, packet: dict[str, Any]) -> None:
        """Record a captured packet.

        Args:
            packet: Dict with 'ts' timestamp key.
        """
        self._packets.append(dict(packet))
        self._fire_evolution({"action": "record", "count": len(self._packets)})

    def replay(
        self,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Replay recorded packets in timestamp order.

        Args:
            start_ts: Optional start time filter.
            end_ts: Optional end time filter.

        Returns:
            List of packets sorted by timestamp.
        """
        sorted_packets = sorted(self._packets, key=lambda p: p.get("ts", 0.0))

        if start_ts is not None or end_ts is not None:
            s = start_ts if start_ts is not None else float("-inf")
            e = end_ts if end_ts is not None else float("inf")
            sorted_packets = [
                p for p in sorted_packets if s <= p.get("ts", 0.0) <= e
            ]

        return sorted_packets

    def clear(self) -> None:
        """Remove all recorded packets."""
        self._packets.clear()

    def get_time_range(self) -> dict[str, float]:
        """Return start/end timestamps of recorded data."""
        if not self._packets:
            return {"start": 0.0, "end": 0.0}
        timestamps = [p.get("ts", 0.0) for p in self._packets]
        return {"start": min(timestamps), "end": max(timestamps)}

    def export_json(self) -> str:
        """Export all packets as JSON string."""
        return json.dumps({
            "packets": self.replay(),
            "count": self.packet_count,
            "time_range": self.get_time_range(),
        })

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
                logger.warning("Evolution callback error (fiddler_replay_engine)")
