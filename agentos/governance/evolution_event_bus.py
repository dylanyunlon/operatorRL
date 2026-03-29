"""
Evolution Event Bus — Pub/sub event bus for cross-module evolution events.
Location: agentos/governance/evolution_event_bus.py
Reference: DI-star event system, Akagi bridge message queue, PARL event bus
"""
from __future__ import annotations
import logging, time, uuid
from typing import Any, Callable, Optional
from collections import defaultdict
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.evolution_event_bus.v1"

class EvolutionEventBus:
    def __init__(self, max_history: int = 1000) -> None:
        self._subscribers: dict[str, dict[str, Callable]] = defaultdict(dict)
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._max_history = max_history
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def subscribe(self, topic: str, handler: Callable[[dict], None]) -> str:
        sub_id = str(uuid.uuid4())
        self._subscribers[topic][sub_id] = handler
        return sub_id

    def unsubscribe(self, sub_id: str) -> None:
        for topic in self._subscribers:
            self._subscribers[topic].pop(sub_id, None)

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        event_with_meta = {**event, "_topic": topic, "_published_at": time.time()}
        # Store history
        self._history[topic].append(event_with_meta)
        if len(self._history[topic]) > self._max_history:
            self._history[topic] = self._history[topic][-self._max_history:]
        # Deliver to topic subscribers
        for sid, handler in list(self._subscribers.get(topic, {}).items()):
            try:
                handler(event)
            except Exception as e:
                logger.warning("Subscriber %s error on topic %s: %s", sid, topic, e)
        # Deliver to wildcard subscribers
        if topic != "*":
            for sid, handler in list(self._subscribers.get("*", {}).items()):
                try:
                    handler(event)
                except Exception as e:
                    logger.warning("Wildcard subscriber %s error: %s", sid, e)
        # Evolution callback
        if self.evolution_callback:
            self.evolution_callback({"type": "event_published", "key": _EVOLUTION_KEY,
                                     "topic": topic, "timestamp": time.time()})

    def get_history(self, topic: str) -> list[dict]:
        return list(self._history.get(topic, []))

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, {}))
