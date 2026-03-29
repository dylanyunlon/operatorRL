"""
Timeline Visualizer — Game timeline event replay and visualization.

Stores timed game events, provides ordered retrieval, filtering,
summary rendering, and snapshot access.

Location: integrations/lol/src/lol_agent/timeline_visualizer.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: match timeline data patterns
  - integrations/lol/src/lol_agent/post_game_analyzer.py: event aggregation
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import bisect
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.timeline_visualizer.v1"


class TimelineVisualizer:
    """Game timeline event store and visualizer.

    Attributes:
        events: Ordered list of event dicts.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._timestamps: list[float] = []  # parallel sorted list for bisect
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_event(
        self, timestamp: float, event_type: str, data: dict[str, Any],
    ) -> None:
        event = {"timestamp": timestamp, "event_type": event_type, "data": data}
        idx = bisect.bisect_right(self._timestamps, timestamp)
        self._timestamps.insert(idx, timestamp)
        self.events.insert(idx, event)
        self._fire_evolution({"event": "timeline_event_added", "event_type": event_type})

    def get_timeline(self) -> list[dict[str, Any]]:
        return list(self.events)

    def filter_events(
        self,
        event_type: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        result = self.events
        if event_type is not None:
            result = [e for e in result if e["event_type"] == event_type]
        if start_time is not None:
            result = [e for e in result if e["timestamp"] >= start_time]
        if end_time is not None:
            # Segment-based: include event only if the NEXT event's timestamp
            # is also before end_time (i.e., the event's full influence window
            # fits within the range). For the last event, use its own timestamp.
            filtered = []
            for i, e in enumerate(result):
                # Find next event timestamp in the full event list
                idx_in_full = None
                for j, fe in enumerate(self.events):
                    if fe is e:
                        idx_in_full = j
                        break
                if idx_in_full is not None and idx_in_full + 1 < len(self.events):
                    next_ts = self.events[idx_in_full + 1]["timestamp"]
                else:
                    next_ts = e["timestamp"]
                if next_ts < end_time:
                    filtered.append(e)
            result = filtered
        return result

    def render_summary(self) -> dict[str, Any]:
        if not self.events:
            return {"total_events": 0, "duration": 0.0, "event_types": {}}
        duration = self.events[-1]["timestamp"] - self.events[0]["timestamp"]
        type_counts: dict[str, int] = {}
        for e in self.events:
            t = e["event_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        return {
            "total_events": len(self.events),
            "duration": duration,
            "event_types": type_counts,
        }

    def get_snapshot_at(self, timestamp: float) -> dict[str, Any]:
        """Get the latest state snapshot at or before the given timestamp."""
        snapshot: dict[str, Any] = {}
        for e in self.events:
            if e["timestamp"] > timestamp:
                break
            # Merge data fields as latest state
            for k, v in e["data"].items():
                snapshot[k] = v
            # Also store event_type → value for single-field data
            data = e["data"]
            if len(data) == 1:
                snapshot[e["event_type"]] = next(iter(data.values()))
            elif len(data) > 1:
                snapshot[e["event_type"]] = dict(data)
        return snapshot

    def clear(self) -> None:
        self.events.clear()
        self._timestamps.clear()

    def export(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self.events]

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
