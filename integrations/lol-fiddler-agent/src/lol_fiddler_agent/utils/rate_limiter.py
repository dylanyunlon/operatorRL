"""
Rate Limiter - Token bucket and sliding window rate limiters.

Provides composable rate limiting strategies for different API
endpoints with different rate limit specifications.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit specification."""
    requests_per_second: float = 20.0
    requests_per_minute: int = 100
    burst_size: int = 10


class TokenBucket:
    """Token bucket rate limiter.

    Allows bursts up to ``capacity`` tokens, refilling at ``rate``
    tokens per second. Thread-safe via asyncio lock.
    """

    def __init__(self, rate: float = 10.0, capacity: int = 20) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._total_allowed = 0
        self._total_denied = 0

    async def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens. Returns True if allowed."""
        async with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                self._total_allowed += 1
                return True
            self._total_denied += 1
            return False

    async def wait_and_acquire(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """Wait until tokens are available, up to timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.acquire(tokens):
                return True
            # Calculate wait time for one token
            wait = 1.0 / self._rate
            await asyncio.sleep(min(wait, deadline - time.monotonic()))
        return False

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens

    @property
    def stats(self) -> dict[str, float]:
        return {
            "available": self.available_tokens,
            "capacity": self._capacity,
            "rate": self._rate,
            "allowed": self._total_allowed,
            "denied": self._total_denied,
        }


class SlidingWindowLimiter:
    """Sliding window rate limiter.

    Tracks request timestamps in a sliding window and rejects
    requests that would exceed the configured limit.
    """

    def __init__(self, max_requests: int = 100, window_seconds: float = 60.0) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max_requests:
            return False
        self._timestamps.append(now)
        return True

    @property
    def current_count(self) -> int:
        now = time.monotonic()
        cutoff = now - self._window
        return sum(1 for t in self._timestamps if t > cutoff)

    @property
    def remaining(self) -> int:
        return max(0, self._max_requests - self.current_count)

    def time_until_available(self) -> float:
        """Seconds until the next request would be allowed."""
        if self.remaining > 0:
            return 0.0
        now = time.monotonic()
        cutoff = now - self._window
        oldest = min((t for t in self._timestamps if t > cutoff), default=now)
        return max(0.0, oldest + self._window - now)


class CompositeRateLimiter:
    """Combines multiple rate limiters (all must allow).

    Example::

        limiter = CompositeRateLimiter()
        limiter.add(TokenBucket(rate=20, capacity=50))
        limiter.add(SlidingWindowLimiter(max_requests=100, window_seconds=120))

        if await limiter.acquire():
            make_request()
    """

    def __init__(self) -> None:
        self._buckets: list[TokenBucket] = []
        self._windows: list[SlidingWindowLimiter] = []

    def add_bucket(self, bucket: TokenBucket) -> None:
        self._buckets.append(bucket)

    def add_window(self, window: SlidingWindowLimiter) -> None:
        self._windows.append(window)

    def add(self, limiter) -> None:
        if isinstance(limiter, TokenBucket):
            self.add_bucket(limiter)
        elif isinstance(limiter, SlidingWindowLimiter):
            self.add_window(limiter)

    async def acquire(self) -> bool:
        """Check all limiters. Returns True only if all allow."""
        # Check windows first (non-async)
        for window in self._windows:
            if not window.allow():
                return False
        # Then check buckets
        for bucket in self._buckets:
            if not await bucket.acquire():
                return False
        return True

    @property
    def stats(self) -> dict[str, Any]:
        from typing import Any
        return {
            "buckets": [b.stats for b in self._buckets],
            "windows": [{"remaining": w.remaining, "count": w.current_count} for w in self._windows],
        }
