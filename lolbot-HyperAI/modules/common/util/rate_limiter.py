"""
modules/common/util/rate_limiter.py — Token Bucket Rate Limiter
=================================================================
lolbot-HyperAI · modules/common/util

查看 cyber/timer/rate_timer.py 上现有 RateTimer 的实现方式, 理解其模式。
从 RateTimer 这个好例子开始。然后, 遵循该模式实现一个新的 TokenBucketLimiter,
让 RiotAPIClient / VoiceNarrator / OverlayRenderer 可以限制输出频率,
并能支持突发 (burst) 容量。接着在 ControlComponent._drain_inputs() 引入速率
限制, 使语音输出能够避免连续轰炸用户 (每5秒最多1条高优先级语音), 同时优化
低优先级语音的丢弃策略。

Architecture position:
    modules/common/util/rate_limiter.py   ← YOU ARE HERE
    ├─ Used by: modules/control/voice_output/voice_priority_queue.py
    ├─ Used by: modules/control/overlay/overlay_renderer.py
    ├─ Used by: integration/riot_api_client.py (API rate limit)
    ├─ Used by: modules/dreamview/api/dreamview_api.py
    └─ Used by: launch/main_loop.py (evolution rate limit)

Apollo reference:
    cyber/timer/rate_timer.h — fixed-interval execution timing
    (no direct rate-limiter, but canbus polls at fixed rate)

Design notes:
    - Token bucket algorithm: tokens refill at steady rate, burst allowed
    - Thread-safe: used from multiple component threads
    - Named limiters can be registered globally for monitoring
    - Supports both blocking (wait) and non-blocking (try_acquire)
    - Configurable per-key rate limiting (e.g. per-API-endpoint)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from cyber.logger.cyber_logger import get_logger

logger = get_logger("common.rate_limiter")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_RATE_HZ = 1.0
_DEFAULT_BURST = 3
_MAX_BURST = 1000
_CLEANUP_INTERVAL_S = 60.0
_PER_KEY_EXPIRY_S = 300.0


# ─── Token Bucket ────────────────────────────────────────────────────────────

@dataclass
class RateLimiterConfig:
    """Configuration for a token bucket rate limiter.

    Attributes:
        name: Human-readable name for monitoring.
        rate_hz: Sustained rate in events per second.
        burst: Maximum burst size (initial token count).
        enable_metrics: Track hit/miss counts.
    """
    name: str = "default"
    rate_hz: float = _DEFAULT_RATE_HZ
    burst: int = _DEFAULT_BURST
    enable_metrics: bool = True

    def __post_init__(self) -> None:
        if self.rate_hz <= 0:
            raise ValueError(f"rate_hz must be > 0, got {self.rate_hz}")
        if self.burst < 1:
            raise ValueError(f"burst must be >= 1, got {self.burst}")
        if self.burst > _MAX_BURST:
            raise ValueError(f"burst must be <= {_MAX_BURST}, got {self.burst}")


@dataclass
class RateLimiterMetrics:
    """Rate limiter usage statistics."""
    name: str = ""
    total_requests: int = 0
    total_allowed: int = 0
    total_denied: int = 0
    total_waited_ms: float = 0.0
    current_tokens: float = 0.0
    rate_hz: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "total_requests": self.total_requests,
            "total_allowed": self.total_allowed,
            "total_denied": self.total_denied,
            "total_waited_ms": round(self.total_waited_ms, 2),
            "current_tokens": round(self.current_tokens, 2),
            "hit_rate": (
                round(self.total_allowed / self.total_requests, 4)
                if self.total_requests > 0 else 0.0
            ),
        }


class TokenBucketLimiter:
    """Token bucket rate limiter.

    Tokens are added at a steady rate (rate_hz). Each request consumes
    one token. If no tokens are available, the request is either denied
    (try_acquire) or the caller waits (acquire).

    Usage::

        limiter = TokenBucketLimiter(RateLimiterConfig(
            name="voice_output",
            rate_hz=0.2,  # max 1 voice every 5 seconds
            burst=2,       # allow 2 rapid-fire voices then throttle
        ))

        if limiter.try_acquire():
            speak(announcement)
        else:
            # Too fast, skip this announcement
            pass
    """

    def __init__(self, config: RateLimiterConfig) -> None:
        self._config = config
        self._tokens: float = float(config.burst)
        self._last_refill_ts: float = time.monotonic()
        self._lock = threading.Lock()
        self._metrics = RateLimiterMetrics(
            name=config.name,
            rate_hz=config.rate_hz,
        )

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def metrics(self) -> RateLimiterMetrics:
        with self._lock:
            self._refill()
            self._metrics.current_tokens = self._tokens
            return self._metrics

    def try_acquire(self, tokens: int = 1) -> bool:
        """Non-blocking: try to consume tokens.

        Returns True if tokens were available, False otherwise.
        """
        with self._lock:
            self._refill()
            self._metrics.total_requests += 1

            if self._tokens >= tokens:
                self._tokens -= tokens
                self._metrics.total_allowed += 1
                return True
            else:
                self._metrics.total_denied += 1
                return False

    def acquire(self, tokens: int = 1, timeout_s: float = 5.0) -> bool:
        """Blocking: wait until tokens are available.

        Args:
            tokens: Number of tokens to consume.
            timeout_s: Maximum wait time. 0 = try_acquire semantics.

        Returns:
            True if tokens were acquired, False on timeout.
        """
        if timeout_s <= 0:
            return self.try_acquire(tokens)

        deadline = time.monotonic() + timeout_s
        wait_start = time.monotonic()

        with self._lock:
            self._metrics.total_requests += 1

            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    wait_ms = (time.monotonic() - wait_start) * 1000.0
                    self._metrics.total_allowed += 1
                    self._metrics.total_waited_ms += wait_ms
                    return True

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._metrics.total_denied += 1
                    return False

                # Calculate wait time until next token
                deficit = tokens - self._tokens
                wait_s = deficit / self._config.rate_hz
                wait_s = min(wait_s, remaining)
                # Release lock while waiting, then reacquire
                self._lock.release()
                try:
                    time.sleep(wait_s)
                finally:
                    self._lock.acquire()

    def reset(self) -> None:
        """Reset tokens to full burst capacity."""
        with self._lock:
            self._tokens = float(self._config.burst)
            self._last_refill_ts = time.monotonic()

    def _refill(self) -> None:
        """Add tokens based on elapsed time (caller holds lock)."""
        now = time.monotonic()
        elapsed = now - self._last_refill_ts
        if elapsed <= 0:
            return
        new_tokens = elapsed * self._config.rate_hz
        self._tokens = min(
            self._tokens + new_tokens, float(self._config.burst),
        )
        self._last_refill_ts = now


# ─── Per-Key Rate Limiter ────────────────────────────────────────────────────

class PerKeyRateLimiter:
    """Rate limiter that tracks limits per key.

    Useful for API rate limiting where different endpoints or
    summoner names have independent rate limits.

    Usage::

        limiter = PerKeyRateLimiter(rate_hz=2.0, burst=5)
        if limiter.try_acquire("summoner_lookup"):
            call_api()
    """

    def __init__(
        self,
        rate_hz: float = _DEFAULT_RATE_HZ,
        burst: int = _DEFAULT_BURST,
        name: str = "per_key",
    ) -> None:
        self._rate_hz = rate_hz
        self._burst = burst
        self._name = name
        self._buckets: Dict[str, TokenBucketLimiter] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def try_acquire(self, key: str, tokens: int = 1) -> bool:
        """Try to acquire tokens for a specific key."""
        bucket = self._get_or_create(key)
        return bucket.try_acquire(tokens)

    def acquire(
        self, key: str, tokens: int = 1, timeout_s: float = 5.0,
    ) -> bool:
        """Blocking acquire for a specific key."""
        bucket = self._get_or_create(key)
        return bucket.acquire(tokens, timeout_s)

    def all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all active keys."""
        with self._lock:
            return {
                key: bucket.metrics.to_dict()
                for key, bucket in self._buckets.items()
            }

    def _get_or_create(self, key: str) -> TokenBucketLimiter:
        """Get or create a bucket for a key (thread-safe)."""
        with self._lock:
            if key not in self._buckets:
                config = RateLimiterConfig(
                    name=f"{self._name}:{key}",
                    rate_hz=self._rate_hz,
                    burst=self._burst,
                )
                self._buckets[key] = TokenBucketLimiter(config)

            # Periodic cleanup of stale buckets
            now = time.monotonic()
            if now - self._last_cleanup > _CLEANUP_INTERVAL_S:
                self._cleanup(now)
                self._last_cleanup = now

            return self._buckets[key]

    def _cleanup(self, now: float) -> None:
        """Remove buckets that haven't been used recently."""
        stale_keys = []
        for key, bucket in self._buckets.items():
            last_ts = bucket._last_refill_ts
            if now - last_ts > _PER_KEY_EXPIRY_S:
                stale_keys.append(key)
        for key in stale_keys:
            del self._buckets[key]
        if stale_keys:
            logger.debug(
                "Cleaned up %d stale rate limiter buckets", len(stale_keys),
            )


# ─── Global registry ─────────────────────────────────────────────────────────

class RateLimiterRegistry:
    """Global registry of named rate limiters for monitoring."""

    _instance: Optional[RateLimiterRegistry] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._limiters: Dict[str, TokenBucketLimiter] = {}

    @classmethod
    def instance(cls) -> RateLimiterRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, limiter: TokenBucketLimiter) -> None:
        self._limiters[limiter.name] = limiter

    def get(self, name: str) -> Optional[TokenBucketLimiter]:
        return self._limiters.get(name)

    def summary(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: lim.metrics.to_dict()
            for name, lim in self._limiters.items()
        }
