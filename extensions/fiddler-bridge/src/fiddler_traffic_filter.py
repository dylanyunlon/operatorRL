"""
Fiddler Traffic Filter — Filter game traffic by domain/port/protocol.

Filters captured traffic to isolate game-relevant packets.
Supports domain, port, and protocol-level filtering with stats tracking.

Location: extensions/fiddler-bridge/src/fiddler_traffic_filter.py

Reference (拿来主义):
  - Akagi/mitm/bridge/unified/bridge.py: traffic routing by game type
  - Akagi/mitm/common.py: packet filtering patterns
  - extensions/fiddler-bridge/src/fiddler_bridge/client.py: SessionStatus categorization
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_traffic_filter.v1"


class FiddlerTrafficFilter:
    """Traffic filter for Fiddler-captured packets.

    Filters by domain, port, and protocol. When no filters are set,
    all traffic passes (permissive default — mirrors Akagi's unified bridge).

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._domain_filters: set[str] = set()
        self._port_filters: set[int] = set()
        self._protocol_filters: set[str] = set()
        self._matched: int = 0
        self._rejected: int = 0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Domain filters ---
    def add_domain_filter(self, domain: str) -> None:
        self._domain_filters.add(domain)
        self._fire_evolution({"action": "add_domain", "domain": domain})

    def remove_domain_filter(self, domain: str) -> None:
        self._domain_filters.discard(domain)

    def get_domain_filters(self) -> list[str]:
        return sorted(self._domain_filters)

    # --- Port filters ---
    def add_port_filter(self, port: int) -> None:
        self._port_filters.add(port)

    def remove_port_filter(self, port: int) -> None:
        self._port_filters.discard(port)

    def get_port_filters(self) -> list[int]:
        return sorted(self._port_filters)

    # --- Protocol filters ---
    def add_protocol_filter(self, protocol: str) -> None:
        self._protocol_filters.add(protocol)

    def get_protocol_filters(self) -> list[str]:
        return sorted(self._protocol_filters)

    # --- Match logic ---
    def matches(self, packet: dict[str, Any]) -> bool:
        """Check if a packet matches the configured filters.

        When no filters are set, all traffic passes.
        When filters are set, packet must match at least one filter
        in each non-empty filter category.

        Args:
            packet: Dict with host, port, protocol keys.

        Returns:
            True if packet passes filters.
        """
        host = packet.get("host", "")
        port = packet.get("port", 0)
        protocol = packet.get("protocol", "")

        domain_ok = (not self._domain_filters) or (host in self._domain_filters)
        port_ok = (not self._port_filters) or (port in self._port_filters)
        proto_ok = (not self._protocol_filters) or (protocol in self._protocol_filters)

        result = domain_ok and port_ok and proto_ok
        if result:
            self._matched += 1
        else:
            self._rejected += 1
        return result

    def get_stats(self) -> dict[str, int]:
        """Return match/reject stats."""
        return {
            "matched": self._matched,
            "rejected": self._rejected,
            "total": self._matched + self._rejected,
        }

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
                logger.warning("Evolution callback error (fiddler_traffic_filter)")
