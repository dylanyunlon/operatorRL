"""
Evolution Replay — Replay evolution events for debugging/analysis.
Location: agentos/governance/evolution_replay.py
Reference: DI-star replay buffer, open_spiel game replay
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.evolution_replay.v1"

class EvolutionReplay:
    def __init__(self, max_events: int = 100000) -> None:
        self._events: list[dict[str, Any]] = []
        self._max_events = max_events
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record(self, event: dict[str, Any]) -> None:
        entry = {**event, "_recorded_at": time.time()}
        self._events.append(entry)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        if self.evolution_callback:
            self.evolution_callback({"type": "event_recorded", "key": _EVOLUTION_KEY, "timestamp": time.time()})

    def event_count(self) -> int:
        return len(self._events)

    def replay(self, *, event_type: str | None = None,
               start_time: float | None = None, end_time: float | None = None) -> list[dict]:
        result = self._events
        if event_type:
            result = [e for e in result if e.get("type") == event_type]
        if start_time is not None:
            result = [e for e in result if e.get("_recorded_at", 0) >= start_time]
        if end_time is not None:
            result = [e for e in result if e.get("_recorded_at", 0) <= end_time]
        return list(result)

    def clear(self) -> None:
        self._events.clear()

    def summary(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for e in self._events:
            by_type[e.get("type", "unknown")] += 1
        return {"total_events": len(self._events), "by_type": dict(by_type)}

    def export_timeline(self) -> list[dict[str, Any]]:
        return [{"timestamp": e.get("_recorded_at", 0), "event": {k: v for k, v in e.items() if not k.startswith("_")}}
                for e in self._events]
