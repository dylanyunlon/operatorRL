"""
Fiddler Evolution Adapter — Protocol data → evolution event conversion.

Converts captured Fiddler traffic packets into evolution events,
classifying traffic quality and computing health scores.

Location: extensions/fiddler-bridge/src/fiddler_evolution_adapter.py

Reference (拿来主义):
  - extensions/fiddler-bridge/src/fiddler_bridge/fiddler_evolution_bridge.py: evolution bridge
  - modules/evolution_loop_abc.py: record/fitness pattern
  - integrations/lol/src/lol_agent/feedback_recorder.py: event classification
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_evolution_adapter.v1"

_ANOMALY_LATENCY_THRESHOLD: float = 5.0
_ANOMALY_STATUS_THRESHOLD: int = 400


class FiddlerEvolutionAdapter:
    """Converts Fiddler traffic data into evolution events.

    Classifies each packet as 'normal' or 'anomaly', tracks quality,
    and provides a quality score for the evolution system.

    Attributes:
        event_count: Number of events converted.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._normal_count: int = 0
        self._anomaly_count: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def event_count(self) -> int:
        return len(self._events)

    def convert(self, packet: dict[str, Any]) -> dict[str, Any]:
        """Convert a captured packet into an evolution event.

        Args:
            packet: Dict with type, status_code, latency, ts keys.

        Returns:
            Evolution event dict with source, classification, etc.
        """
        latency = packet.get("latency", 0.0)
        status_code = packet.get("status_code", 200)

        is_anomaly = (
            latency > _ANOMALY_LATENCY_THRESHOLD
            or (isinstance(status_code, int) and status_code >= _ANOMALY_STATUS_THRESHOLD)
        )

        classification = "anomaly" if is_anomaly else "normal"

        if is_anomaly:
            self._anomaly_count += 1
        else:
            self._normal_count += 1

        event = {
            "event_type": "traffic_observation",
            "source": "fiddler",
            "classification": classification,
            "latency": latency,
            "status_code": status_code,
            "timestamp": packet.get("ts", time.time()),
        }
        self._events.append(event)
        self._fire_evolution({"action": "convert", "classification": classification})
        return event

    def batch_convert(self, packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert multiple packets in batch.

        Args:
            packets: List of packet dicts.

        Returns:
            List of evolution event dicts.
        """
        return [self.convert(p) for p in packets]

    def get_quality_score(self) -> float:
        """Compute traffic quality score (normal / total).

        Returns:
            Float between 0.0 and 1.0, or 0.0 if no events.
        """
        total = self._normal_count + self._anomaly_count
        if total == 0:
            return 0.0
        return self._normal_count / total

    def reset(self) -> None:
        """Reset all counters and events."""
        self._events.clear()
        self._normal_count = 0
        self._anomaly_count = 0

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
                logger.warning("Evolution callback error (fiddler_evolution_adapter)")
