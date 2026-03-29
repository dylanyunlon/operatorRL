"""
Packet Correlation — Request-response pairing + timing analysis.

Correlates HTTP requests with their responses by ID, computes latencies,
and tracks orphaned requests/responses.

Location: extensions/protocol-decoder/src/packet_correlation.py

Reference (拿来主义):
  - Akagi/mitm/client.py: request-response flow tracking
  - extensions/fiddler-bridge/src/fiddler_bridge/combat_calculator.py: timing analysis
  - agentos/governance/telemetry_collector.py: metric pairing pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.packet_correlation.v1"


class PacketCorrelation:
    """Request-response packet correlator.

    Pairs requests with responses by ID, computes latency,
    and identifies orphaned requests/responses.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._pairs: list[dict[str, Any]] = []
        self._orphan_responses: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def pending_count(self) -> int:
        return len(self._pending_requests)

    def record_request(self, request_id: str, data: dict[str, Any]) -> None:
        """Record an outgoing request.

        Args:
            request_id: Unique request identifier.
            data: Request data including 'ts' timestamp.
        """
        self._pending_requests[request_id] = dict(data)

    def record_response(self, request_id: str, data: dict[str, Any]) -> None:
        """Record an incoming response and pair with request if found.

        Args:
            request_id: Matching request identifier.
            data: Response data including 'ts' timestamp.
        """
        if request_id in self._pending_requests:
            req = self._pending_requests.pop(request_id)
            latency = data.get("ts", 0.0) - req.get("ts", 0.0)
            pair = {
                "request_id": request_id,
                "request": req,
                "response": data,
                "latency": latency,
            }
            self._pairs.append(pair)
            self._fire_evolution({"action": "paired", "request_id": request_id, "latency": latency})
        else:
            self._orphan_responses.append({
                "request_id": request_id,
                "response": data,
            })

    def get_pairs(self) -> list[dict[str, Any]]:
        """Return all request-response pairs."""
        return list(self._pairs)

    def get_orphan_requests(self) -> list[dict[str, Any]]:
        """Return requests without matching responses."""
        return [
            {"request_id": rid, "request": data}
            for rid, data in self._pending_requests.items()
        ]

    def get_orphan_responses(self) -> list[dict[str, Any]]:
        """Return responses without matching requests."""
        return list(self._orphan_responses)

    def average_latency(self) -> float:
        """Compute average latency across all pairs.

        Returns:
            Average latency in seconds, or 0.0 if no pairs.
        """
        if not self._pairs:
            return 0.0
        return sum(p["latency"] for p in self._pairs) / len(self._pairs)

    def clear(self) -> None:
        """Clear all state."""
        self._pending_requests.clear()
        self._pairs.clear()
        self._orphan_responses.clear()

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
                logger.warning("Evolution callback error (packet_correlation)")
