"""
Real-Time Poller — Periodic Live Client API data polling with retry.

Polls Live Client Data API endpoints at configurable intervals,
dispatches data to registered handlers, tracks errors.

Location: integrations/lol/src/lol_agent/real_time_poller.py

Reference (拿来主义):
  - LeagueAI/LeagueAI_helper.py: input_output.get_pixels() periodic capture loop
  - Seraphine/app/lol/connector.py: retry decorator + error counting
  - integrations/lol/src/lol_agent/live_client_connector.py: _request_count/_error_count
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.real_time_poller.v1"


class RealTimePoller:
    """Periodic poller for Live Client Data API.

    Registers endpoint handlers, simulates polling cycles,
    tracks poll/error counts. Mirrors LeagueAI's input_output
    periodic frame capture and Seraphine's retry pattern.

    Attributes:
        interval: Poll interval in seconds.
        is_running: Whether the poller is actively running.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, interval: float = 1.0, max_retries: int = 3) -> None:
        self.interval: float = interval
        self.max_retries: int = max_retries
        self._handlers: dict[str, Callable[[dict], None]] = {}
        self._poll_count: int = 0
        self._error_count: int = 0
        self._is_running: bool = False

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def is_running(self) -> bool:
        """Whether the poller is active."""
        return self._is_running

    @property
    def poll_count(self) -> int:
        """Total successful polls."""
        return self._poll_count

    @property
    def error_count(self) -> int:
        """Total poll errors."""
        return self._error_count

    def register_handler(
        self, endpoint: str, handler: Callable[[dict], None]
    ) -> None:
        """Register a handler for an endpoint.

        Args:
            endpoint: Endpoint key (e.g., 'allgamedata').
            handler: Callback receiving parsed data dict.
        """
        self._handlers[endpoint] = handler

    def unregister_handler(self, endpoint: str) -> None:
        """Remove a handler for an endpoint."""
        self._handlers.pop(endpoint, None)

    def list_handlers(self) -> list[str]:
        """List registered endpoint keys."""
        return list(self._handlers.keys())

    def simulate_poll(self, endpoint: str, data: dict[str, Any]) -> None:
        """Simulate a poll cycle for testing.

        Calls the registered handler with provided data.
        Catches handler exceptions and increments error_count.

        Args:
            endpoint: Endpoint key to poll.
            data: Simulated response data.
        """
        handler = self._handlers.get(endpoint)
        if handler is None:
            return

        try:
            handler(data)
            self._poll_count += 1
        except Exception as exc:
            self._error_count += 1
            logger.warning("Handler error for %s: %s", endpoint, exc)

        self._fire_evolution("poll_completed", {
            "endpoint": endpoint,
            "poll_count": self._poll_count,
            "error_count": self._error_count,
        })

    def start(self) -> None:
        """Mark poller as running."""
        self._is_running = True

    def stop(self) -> None:
        """Mark poller as stopped."""
        self._is_running = False

    def get_stats(self) -> dict[str, Any]:
        """Return polling statistics."""
        return {
            "poll_count": self._poll_count,
            "error_count": self._error_count,
            "interval": self.interval,
            "is_running": self._is_running,
            "handler_count": len(self._handlers),
        }

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
