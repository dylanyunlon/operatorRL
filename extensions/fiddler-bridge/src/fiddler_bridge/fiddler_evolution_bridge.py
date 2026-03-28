"""
Fiddler Evolution Bridge — Protocol data to training span closed loop.

Bridges Fiddler-captured protocol data to AgentLightning training spans,
building state-action-reward tuples from ingested events with generation
tagging and batch export.

Location: extensions/fiddler-bridge/src/fiddler_bridge/fiddler_evolution_bridge.py
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.fiddler_evolution_bridge.v1"


class FiddlerEvolutionBridge:
    """Bridge protocol data to evolution training loop.

    Ingests typed events (state/action/reward), builds training spans
    from event triples, tags with generation metadata, and exports
    in AgentLightning-compatible format.
    """

    def __init__(self, generation: int = 0) -> None:
        self.generation = generation
        self._events: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def ingest_event(self, event: dict[str, Any]) -> None:
        """Ingest a protocol event.

        Args:
            event: Event dict with type, data, and timestamp keys.
        """
        self._events.append(event)

    def get_collected_events(self) -> list[dict[str, Any]]:
        """Get all collected events.

        Returns:
            List of event dicts.
        """
        return list(self._events)

    def build_training_spans(self) -> list[dict[str, Any]]:
        """Build training spans from collected events.

        Groups events into (state, action, reward) triples.

        Returns:
            List of training span dicts.
        """
        states = [e for e in self._events if e.get("type") == "state"]
        actions = [e for e in self._events if e.get("type") == "action"]
        rewards = [e for e in self._events if e.get("type") == "reward"]

        spans: list[dict[str, Any]] = []
        n = min(len(states), len(actions), len(rewards))
        for i in range(n):
            span: dict[str, Any] = {
                "state": states[i].get("data", {}),
                "action": actions[i].get("data", {}),
                "reward": rewards[i].get("data", {}).get("value", 0.0),
                "timestamp": states[i].get("timestamp", 0.0),
                "generation": self.generation,
            }
            spans.append(span)
        return spans

    def export_batch(self, batch_size: int = 32) -> list[dict[str, Any]]:
        """Export a batch of training spans.

        Args:
            batch_size: Maximum number of spans.

        Returns:
            List of training span dicts.
        """
        spans = self.build_training_spans()
        return spans[:batch_size]

    def export_json(self) -> str:
        """Export all training spans as JSON.

        Returns:
            JSON string of spans list.
        """
        spans = self.build_training_spans()
        return json.dumps(spans, ensure_ascii=False, indent=2)

    def get_stats(self) -> dict[str, int]:
        """Get bridge statistics.

        Returns:
            Stats dict with total_events and event type counts.
        """
        total = len(self._events)
        by_type: dict[str, int] = {}
        for e in self._events:
            t = e.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        return {"total_events": total, **by_type}

    def reset(self) -> None:
        """Reset all collected events."""
        self._events.clear()

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "fiddler_evolution_bridge",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
