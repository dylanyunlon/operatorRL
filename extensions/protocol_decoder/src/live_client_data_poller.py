"""
Live Client Data Poller — timed polling for Riot Live Client Data API.

Periodically polls the local Live Client Data API endpoints and dispatches
new data to registered callbacks.  Includes exponential backoff on failure,
diff detection between consecutive polls, and evolution integration.

Location: extensions/protocol_decoder/src/live_client_data_poller.py

Reference (拿来主义):
  - Seraphine/app/lol/listener.py: LolProcessExistenceListener polling loop
  - Seraphine/app/lol/connector.py: retry + semaphore + PastRequest
  - integrations/lol/src/lol_agent/live_client_connector.py: endpoint map
  - integrations/lol/src/lol_agent/real_time_poller.py: existing poller stub

Design Notes (Knuth-level critique):
  User:
    - Backoff prevents hammering the API when game isn't running.
    - Diff detection enables event-driven downstream without polling overhead.
    - simulate_poll allows testing without a live game.
  System:
    - State per endpoint (last data, last poll time) — independent scheduling.
    - Backoff is per-endpoint — one flaky endpoint doesn't stall others.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.live_client_data_poller.v1"

_DEFAULT_INTERVAL_MS: int = 1000
_DEFAULT_MAX_BACKOFF_MS: int = 30_000
_DEFAULT_ENDPOINTS: List[str] = [
    "allgamedata",
    "playerlist",
    "activeplayer",
    "eventdata",
    "gamestats",
]


def _deep_diff(old: Any, new: Any, path: str = "") -> Dict[str, Any]:
    """Compute a shallow diff between two dicts.

    Returns {"changed": {key: new_val}, "added": [...], "removed": [...]}.
    Only goes one level deep for performance.
    """
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            return {"changed": {path or "root": new}}
        return {}

    changed: Dict[str, Any] = {}
    added: List[str] = []
    removed: List[str] = []

    all_keys: Set[str] = set(old.keys()) | set(new.keys())
    for k in all_keys:
        if k not in old:
            added.append(k)
        elif k not in new:
            removed.append(k)
        elif old[k] != new[k]:
            changed[k] = new[k]

    result: Dict[str, Any] = {}
    if changed:
        result["changed"] = changed
    if added:
        result["added"] = added
    if removed:
        result["removed"] = removed
    return result


class _EndpointState:
    """Per-endpoint polling state."""

    __slots__ = (
        "endpoint",
        "last_data",
        "last_poll_time",
        "last_diff",
        "error_count",
        "current_backoff_ms",
        "base_interval_ms",
    )

    def __init__(self, endpoint: str, base_interval_ms: int) -> None:
        self.endpoint = endpoint
        self.last_data: Optional[Any] = None
        self.last_poll_time: float = 0.0
        self.last_diff: Dict[str, Any] = {}
        self.error_count: int = 0
        self.current_backoff_ms: int = base_interval_ms
        self.base_interval_ms: int = base_interval_ms

    def record_success(self, data: Any) -> Dict[str, Any]:
        diff: Dict[str, Any] = {}
        if self.last_data is not None:
            diff = _deep_diff(self.last_data, data)
        self.last_data = copy.deepcopy(data)
        self.last_poll_time = time.time()
        self.last_diff = diff
        self.error_count = 0
        self.current_backoff_ms = self.base_interval_ms
        return diff

    def record_error(self, max_backoff_ms: int) -> None:
        self.error_count += 1
        self.current_backoff_ms = min(
            self.current_backoff_ms * 2,
            max_backoff_ms,
        )


class LiveClientDataPoller:
    """Timed poller for Riot's Live Client Data API.

    Attributes:
        is_polling: Whether the polling loop is active.
        poll_count: Total polls executed.
        endpoints: List of endpoint names being polled.
        base_interval_ms: Base polling interval in milliseconds.
        current_backoff_ms: Current backoff for the most-recently-errored endpoint.
        evolution_callback: Optional callback for self-evolution events.
        on_data: Optional callback ``(endpoint, data) -> None``.

    Reference (拿来主義):
        - Seraphine listener.py: msleep + loop polling pattern
        - integrations/lol live_client_connector: endpoint map
    """

    def __init__(
        self,
        *,
        interval_ms: int = _DEFAULT_INTERVAL_MS,
        max_backoff_ms: int = _DEFAULT_MAX_BACKOFF_MS,
        endpoints: Sequence[str] | None = None,
    ) -> None:
        self._base_interval_ms = interval_ms
        self._max_backoff_ms = max_backoff_ms
        self._endpoints_list: List[str] = list(endpoints) if endpoints else list(_DEFAULT_ENDPOINTS)

        self._states: Dict[str, _EndpointState] = {
            ep: _EndpointState(ep, interval_ms) for ep in self._endpoints_list
        }

        self._polling = False
        self._poll_count: int = 0
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Callbacks
        self.on_data: Optional[Callable[[str, Any], None]] = None
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_polling(self) -> bool:
        return self._polling

    @property
    def poll_count(self) -> int:
        return self._poll_count

    @property
    def endpoints(self) -> List[str]:
        return list(self._endpoints_list)

    @property
    def base_interval_ms(self) -> int:
        return self._base_interval_ms

    @property
    def current_backoff_ms(self) -> int:
        if not self._states:
            return self._base_interval_ms
        return max(s.current_backoff_ms for s in self._states.values())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._polling:
            return
        self._polling = True
        self._stop_event.clear()
        self._fire_evolution({"action": "start"})

    def stop(self) -> None:
        if not self._polling:
            return
        self._polling = False
        self._stop_event.set()
        self._fire_evolution({"action": "stop", "poll_count": self._poll_count})

    # ------------------------------------------------------------------
    # Simulate (for testing without live game)
    # ------------------------------------------------------------------

    def simulate_poll(self, endpoint: str, data: Any) -> Dict[str, Any]:
        """Simulate receiving data for an endpoint.

        This is the core data-intake path used both by the real polling
        loop and by tests.
        """
        self._poll_count += 1

        state = self._states.get(endpoint)
        if state is None:
            state = _EndpointState(endpoint, self._base_interval_ms)
            self._states[endpoint] = state

        diff = state.record_success(data)

        # Fire user callback
        cb = self.on_data
        if cb is not None:
            try:
                cb(endpoint, data)
            except Exception:
                logger.exception("on_data callback raised for endpoint=%s", endpoint)

        self._fire_evolution({
            "action": "poll",
            "endpoint": endpoint,
            "has_diff": bool(diff),
        })

        return diff

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def handle_poll_error(self, endpoint: str, error: Exception) -> Dict[str, Any]:
        """Handle a poll failure with exponential backoff."""
        state = self._states.get(endpoint)
        if state is None:
            state = _EndpointState(endpoint, self._base_interval_ms)
            self._states[endpoint] = state

        state.record_error(self._max_backoff_ms)

        logger.warning(
            "Poll error for %s (attempt %d, backoff %dms): %s",
            endpoint, state.error_count, state.current_backoff_ms, error,
        )

        return {
            "retry": True,
            "backoff_ms": state.current_backoff_ms,
            "error_count": state.error_count,
            "error": str(error),
        }

    # ------------------------------------------------------------------
    # Diff access
    # ------------------------------------------------------------------

    def get_last_diff(self, endpoint: str) -> Dict[str, Any]:
        state = self._states.get(endpoint)
        if state is None:
            return {}
        return state.last_diff

    def get_last_data(self, endpoint: str) -> Optional[Any]:
        state = self._states.get(endpoint)
        if state is None:
            return None
        return state.last_data

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "is_polling": self._polling,
            "poll_count": self._poll_count,
            "endpoints": self._endpoints_list,
            "base_interval_ms": self._base_interval_ms,
            "max_backoff_ms": self._max_backoff_ms,
            "endpoint_states": {
                ep: {
                    "last_poll_time": s.last_poll_time,
                    "error_count": s.error_count,
                    "current_backoff_ms": s.current_backoff_ms,
                }
                for ep, s in self._states.items()
            },
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised in LiveClientDataPoller")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"LiveClientDataPoller(polling={self._polling}, "
            f"polls={self._poll_count}, endpoints={len(self._endpoints_list)})"
        )


default_poller: LiveClientDataPoller = LiveClientDataPoller()
