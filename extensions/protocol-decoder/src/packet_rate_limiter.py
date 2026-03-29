"""
Packet Rate Limiter — Rate-limit protocol decode to prevent overload.
Location: extensions/protocol-decoder/src/packet_rate_limiter.py
Reference: Akagi traffic throttling, DI-star worker rate limits
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "extensions.protocol_decoder.packet_rate_limiter.v1"

class PacketRateLimiter:
    def __init__(self, max_per_second: int = 100, burst: int = 0,
                 global_max_per_second: int = 0) -> None:
        self._max_ps = max_per_second
        self._burst = burst if burst > 0 else max_per_second
        self._global_max = global_max_per_second
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._global_window: list[float] = []
        self._stats: dict[str, dict[str, int]] = defaultdict(lambda: {"allowed": 0, "rejected": 0})
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def _clean_window(self, window: list[float], now: float) -> list[float]:
        return [t for t in window if now - t < 1.0]

    def allow(self, protocol: str) -> bool:
        now = time.time()
        self._windows[protocol] = self._clean_window(self._windows[protocol], now)
        self._global_window = self._clean_window(self._global_window, now)

        # Check global limit
        if self._global_max > 0 and len(self._global_window) >= self._global_max:
            self._stats[protocol]["rejected"] += 1
            return False

        # Check per-protocol limit (use burst for initial window)
        if len(self._windows[protocol]) >= self._burst:
            self._stats[protocol]["rejected"] += 1
            return False

        self._windows[protocol].append(now)
        self._global_window.append(now)
        self._stats[protocol]["allowed"] += 1
        if self.evolution_callback:
            self.evolution_callback({"type": "packet_allowed", "key": _EVOLUTION_KEY,
                                     "protocol": protocol, "timestamp": now})
        return True

    def get_stats(self, protocol: str) -> dict[str, int]:
        return dict(self._stats[protocol])

    def reset(self, protocol: str) -> None:
        self._windows[protocol].clear()
        self._stats[protocol] = {"allowed": 0, "rejected": 0}

    def remaining(self, protocol: str) -> int:
        now = time.time()
        self._windows[protocol] = self._clean_window(self._windows[protocol], now)
        return max(0, self._max_ps - len(self._windows[protocol]))
