"""
Realtime Stream — Capture → Decode → Event streaming pipeline.

Provides a 14fps (configurable) event streaming pipeline that captures
protocol data, decodes it, and dispatches typed events to registered handlers.

Location: extensions/fiddler-bridge/src/fiddler_bridge/realtime_stream.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.realtime_stream.v1"


class RealtimeStream:
    """Realtime event streaming pipeline.

    Captures protocol data at a target FPS, decodes packets, and
    dispatches typed events to registered handler callables.
    """

    def __init__(
        self,
        target_fps: int = 14,
        buffer_size: int = 1024,
    ) -> None:
        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps if target_fps > 0 else 1.0
        self.buffer_size = buffer_size
        self.handlers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
        self._stats: dict[str, int] = {"events_dispatched": 0, "events_dropped": 0}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_handler(
        self, event_type: str, handler: Callable[[dict], None]
    ) -> None:
        """Register an event handler for a specific event type.

        Args:
            event_type: Event type string to listen for.
            handler: Callable that receives the event data dict.
        """
        self.handlers[event_type].append(handler)

    def dispatch_event(self, event: dict[str, Any]) -> None:
        """Dispatch an event to registered handlers.

        Args:
            event: Event dict with 'type' and 'data' keys.
        """
        self._stats["events_dispatched"] += 1
        event_type = event.get("type", "")
        for handler in self.handlers.get(event_type, []):
            try:
                handler(event)
            except Exception as exc:
                logger.warning("Handler error for %s: %s", event_type, exc)

    def get_stats(self) -> dict[str, int]:
        """Get stream statistics.

        Returns:
            Stats dict with events_dispatched and events_dropped.
        """
        return dict(self._stats)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "realtime_stream",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
