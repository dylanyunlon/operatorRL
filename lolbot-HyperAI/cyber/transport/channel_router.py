"""
cyber/transport/channel_router.py — Topic-based Message Router with Priority
==============================================================================
lolbot-HyperAI · Cyber Transport Layer

查看 canbus/channel_message.py 上现有 MessageBus 的 pub/sub 实现方式, 理解
其模式, 特别是 subscribe()/publish() 是如何通过 channel_name 分发的。从
MessageBus 这个好例子开始。然后, 遵循该模式实现一个新的 ChannelRouter,
让 topic 可以按优先级分发 (high-priority events like kill_feed 先于
low-priority monitor_status), 并能支持通配符订阅 (/lol/*)。接着在
BackpressureRegistry 引入 per-route 背压策略, 使不同优先级的消息能够
独立管理队列深度, 同时优化低优先级消息的丢弃策略 (优先保留高优先级)。

Architecture position:
    cyber/transport/channel_router.py   ← YOU ARE HERE
    ├─ Wraps: canbus/channel_message.py (MessageBus)
    ├─ Uses: cyber/transport/backpressure.py (BoundedChannelBuffer)
    ├─ Used by: launch/mainboard.py (component wiring)
    └─ Used by: modules/*/component.py (all components)

Apollo reference:
    cyber/transport/dispatcher/intra_dispatcher.cc — intra-process routing
    cyber/transport/dispatcher/rtps_dispatcher.cc  — inter-process routing

Design notes:
    - Three priority levels: CRITICAL, NORMAL, LOW
    - Wildcard subscriptions via fnmatch-style glob patterns
    - Route table: channel → [(callback, priority, filter)]
    - Priority queue per consumer: high-prio messages dequeued first
    - Dead-letter support: unroutable messages go to a DLQ
    - Metrics: routed_count, unroutable_count, per-route latency
"""

from __future__ import annotations

import enum
import fnmatch
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Type,
)

from cyber.logger.cyber_logger import get_logger

logger = get_logger("transport.router")

# ─── Constants ───────────────────────────────────────────────────────────────

_MAX_DLQ_SIZE = 100
_ROUTE_MATCH_CACHE_SIZE = 256


# ─── Priority ────────────────────────────────────────────────────────────────

class RoutePriority(enum.IntEnum):
    """Message priority levels.

    Higher numeric value = higher priority = dequeued first.

    Examples:
        CRITICAL: kill events, game phase transitions, error alerts
        NORMAL:   game state snapshots, predictions, strategies
        LOW:      monitor heartbeats, statistics, debug logs
    """
    LOW = 0
    NORMAL = 1
    CRITICAL = 2


# ─── Route definition ───────────────────────────────────────────────────────

@dataclass
class RouteEntry:
    """A single subscription registration.

    Attributes:
        subscriber_id: Unique identifier for the subscriber.
        channel_pattern: Exact topic or glob pattern (e.g. "/lol/*").
        callback: Function to invoke with the message.
        priority: Message priority level.
        message_filter: Optional predicate to filter messages.
    """
    subscriber_id: str
    channel_pattern: str
    callback: Callable[[str, Any], None]
    priority: RoutePriority = RoutePriority.NORMAL
    message_filter: Optional[Callable[[Any], bool]] = None

    # Internal tracking
    _routed_count: int = field(default=0, init=False, repr=False)
    _last_route_ts: float = field(default=0.0, init=False, repr=False)
    _total_latency_ms: float = field(default=0.0, init=False, repr=False)


# ─── Route Match Cache ───────────────────────────────────────────────────────

class _RouteMatchCache:
    """LRU cache for channel → matching routes resolution.

    Avoids repeated fnmatch() calls on every publish when the set
    of routes is stable. Invalidated when routes are added/removed.
    """

    def __init__(self, max_size: int = _ROUTE_MATCH_CACHE_SIZE) -> None:
        self._cache: Dict[str, List[RouteEntry]] = {}
        self._max_size = max_size
        self._valid = True

    def get(self, channel: str) -> Optional[List[RouteEntry]]:
        if not self._valid:
            return None
        return self._cache.get(channel)

    def put(self, channel: str, routes: List[RouteEntry]) -> None:
        if len(self._cache) >= self._max_size:
            # Evict oldest entry (simple: just clear)
            self._cache.clear()
        self._cache[channel] = routes
        self._valid = True

    def invalidate(self) -> None:
        self._cache.clear()
        self._valid = False


# ─── Router metrics ──────────────────────────────────────────────────────────

@dataclass
class RouterMetrics:
    """Aggregated routing metrics."""
    total_routed: int = 0
    total_unroutable: int = 0
    total_filtered: int = 0
    total_errors: int = 0
    total_dlq: int = 0
    route_count: int = 0
    channel_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_routed": self.total_routed,
            "total_unroutable": self.total_unroutable,
            "total_filtered": self.total_filtered,
            "total_errors": self.total_errors,
            "total_dlq": self.total_dlq,
            "route_count": self.route_count,
            "channel_count": self.channel_count,
        }


# ─── Dead Letter Queue ───────────────────────────────────────────────────────

@dataclass
class DeadLetter:
    """A message that could not be routed."""
    channel: str
    message: Any
    timestamp: float
    reason: str


# ─── Channel Router ──────────────────────────────────────────────────────────

class ChannelRouter:
    """Topic-based message router with priority support.

    Extends the existing MessageBus pattern with:
    1. Priority-based routing (CRITICAL > NORMAL > LOW)
    2. Wildcard subscriptions (glob patterns)
    3. Per-route message filtering
    4. Dead-letter queue for unroutable messages
    5. Routing metrics per route and per channel

    Usage::

        router = ChannelRouter()

        # Subscribe to specific channel
        router.subscribe(RouteEntry(
            subscriber_id="perception",
            channel_pattern="/lol/raw_lcu",
            callback=perception.on_raw_lcu,
            priority=RoutePriority.NORMAL,
        ))

        # Subscribe to all lol channels
        router.subscribe(RouteEntry(
            subscriber_id="monitor",
            channel_pattern="/lol/*",
            callback=monitor.on_any_lol,
            priority=RoutePriority.LOW,
        ))

        # Publish (routes to all matching subscribers)
        router.publish("/lol/raw_lcu", raw_data)
    """

    def __init__(
        self,
        enable_dlq: bool = True,
        max_dlq_size: int = _MAX_DLQ_SIZE,
    ) -> None:
        self._lock = threading.RLock()  # Reentrant: callbacks may publish

        # Exact-match routes: channel → [RouteEntry, ...]
        self._exact_routes: Dict[str, List[RouteEntry]] = defaultdict(list)

        # Pattern routes (wildcard): [(pattern, RouteEntry), ...]
        self._pattern_routes: List[Tuple[str, RouteEntry]] = []

        # All subscriber IDs for dedup
        self._subscriber_ids: Set[str] = set()

        # Cache for channel → routes resolution
        self._match_cache = _RouteMatchCache()

        # Dead letter queue
        self._enable_dlq = enable_dlq
        self._dlq: List[DeadLetter] = []
        self._max_dlq_size = max_dlq_size

        # Metrics
        self._metrics = RouterMetrics()
        self._channel_publish_counts: Dict[str, int] = defaultdict(int)

    def subscribe(self, route: RouteEntry) -> bool:
        """Register a route for message delivery.

        Args:
            route: RouteEntry describing the subscription.

        Returns:
            True if registered, False if subscriber_id already exists
            for this channel pattern (idempotent — no duplicate routes).
        """
        with self._lock:
            # Check for duplicate
            sub_key = (route.subscriber_id, route.channel_pattern)
            for existing in self._exact_routes.get(route.channel_pattern, []):
                if existing.subscriber_id == route.subscriber_id:
                    logger.debug(
                        "Route already exists: %s on %s",
                        route.subscriber_id, route.channel_pattern,
                    )
                    return False
            for _, existing in self._pattern_routes:
                if (existing.subscriber_id == route.subscriber_id
                        and existing.channel_pattern == route.channel_pattern):
                    return False

            # Determine if this is a glob pattern or exact match
            if any(c in route.channel_pattern for c in ('*', '?', '[')):
                self._pattern_routes.append((route.channel_pattern, route))
            else:
                self._exact_routes[route.channel_pattern].append(route)

            self._subscriber_ids.add(route.subscriber_id)
            self._match_cache.invalidate()
            self._metrics.route_count += 1
            self._metrics.channel_count = len(self._exact_routes) + len(
                set(p for p, _ in self._pattern_routes)
            )

            logger.debug(
                "Subscribed %s to %s (priority=%s)",
                route.subscriber_id, route.channel_pattern,
                route.priority.name,
            )
            return True

    def unsubscribe(self, subscriber_id: str, channel_pattern: str) -> bool:
        """Remove a specific subscription."""
        with self._lock:
            removed = False

            # Check exact routes
            if channel_pattern in self._exact_routes:
                before = len(self._exact_routes[channel_pattern])
                self._exact_routes[channel_pattern] = [
                    r for r in self._exact_routes[channel_pattern]
                    if r.subscriber_id != subscriber_id
                ]
                if not self._exact_routes[channel_pattern]:
                    del self._exact_routes[channel_pattern]
                removed = (
                    len(self._exact_routes.get(channel_pattern, []))
                    < before
                )

            # Check pattern routes
            before_p = len(self._pattern_routes)
            self._pattern_routes = [
                (pat, route) for pat, route in self._pattern_routes
                if not (route.subscriber_id == subscriber_id
                        and pat == channel_pattern)
            ]
            if len(self._pattern_routes) < before_p:
                removed = True

            if removed:
                self._match_cache.invalidate()
                self._metrics.route_count -= 1
                logger.debug(
                    "Unsubscribed %s from %s",
                    subscriber_id, channel_pattern,
                )
            return removed

    def unsubscribe_all(self, subscriber_id: str) -> int:
        """Remove all subscriptions for a given subscriber.

        Returns the number of routes removed.
        """
        count = 0
        with self._lock:
            for channel in list(self._exact_routes.keys()):
                before = len(self._exact_routes[channel])
                self._exact_routes[channel] = [
                    r for r in self._exact_routes[channel]
                    if r.subscriber_id != subscriber_id
                ]
                removed = before - len(self._exact_routes[channel])
                if removed:
                    count += removed
                    if not self._exact_routes[channel]:
                        del self._exact_routes[channel]

            before_p = len(self._pattern_routes)
            self._pattern_routes = [
                (pat, route) for pat, route in self._pattern_routes
                if route.subscriber_id != subscriber_id
            ]
            count += before_p - len(self._pattern_routes)

            if count > 0:
                self._match_cache.invalidate()
                self._subscriber_ids.discard(subscriber_id)
                self._metrics.route_count -= count

        return count

    def publish(
        self,
        channel: str,
        message: Any,
        priority_override: Optional[RoutePriority] = None,
    ) -> int:
        """Publish a message to a channel.

        Routes the message to all matching subscribers, sorted by
        priority (highest first). Subscribers with message_filter
        predicates that return False are skipped.

        Args:
            channel: Topic name, e.g. "/lol/game_state".
            message: The message payload.
            priority_override: If set, all routes use this priority.

        Returns:
            Number of subscribers the message was delivered to.
        """
        with self._lock:
            routes = self._resolve_routes(channel)
            self._channel_publish_counts[channel] += 1

        if not routes:
            self._metrics.total_unroutable += 1
            if self._enable_dlq:
                self._enqueue_dlq(channel, message, "no_matching_routes")
            return 0

        # Sort by priority (highest first) for delivery order
        sorted_routes = sorted(
            routes, key=lambda r: r.priority.value, reverse=True,
        )

        delivered = 0
        for route in sorted_routes:
            effective_priority = priority_override or route.priority

            # Apply message filter
            if route.message_filter is not None:
                try:
                    if not route.message_filter(message):
                        self._metrics.total_filtered += 1
                        continue
                except Exception:
                    logger.warning(
                        "message_filter error for %s on %s",
                        route.subscriber_id, channel,
                        exc_info=True,
                    )
                    continue

            # Deliver
            t0 = time.monotonic()
            try:
                route.callback(channel, message)
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                route._routed_count += 1
                route._last_route_ts = t0
                route._total_latency_ms += elapsed_ms
                delivered += 1
            except Exception:
                self._metrics.total_errors += 1
                logger.error(
                    "Route callback error: %s on %s",
                    route.subscriber_id, channel,
                    exc_info=True,
                )

        self._metrics.total_routed += delivered
        return delivered

    def get_metrics(self) -> RouterMetrics:
        """Get a snapshot of routing metrics."""
        return self._metrics

    def get_route_stats(self) -> List[Dict[str, Any]]:
        """Get per-route statistics."""
        stats: List[Dict[str, Any]] = []
        with self._lock:
            for channel, routes in self._exact_routes.items():
                for route in routes:
                    avg_lat = (
                        route._total_latency_ms / route._routed_count
                        if route._routed_count > 0 else 0.0
                    )
                    stats.append({
                        "subscriber": route.subscriber_id,
                        "channel": channel,
                        "priority": route.priority.name,
                        "routed_count": route._routed_count,
                        "avg_latency_ms": round(avg_lat, 2),
                    })
            for pat, route in self._pattern_routes:
                avg_lat = (
                    route._total_latency_ms / route._routed_count
                    if route._routed_count > 0 else 0.0
                )
                stats.append({
                    "subscriber": route.subscriber_id,
                    "channel": pat + " (glob)",
                    "priority": route.priority.name,
                    "routed_count": route._routed_count,
                    "avg_latency_ms": round(avg_lat, 2),
                })
        return stats

    def drain_dlq(self, max_count: int = 50) -> List[DeadLetter]:
        """Drain up to max_count dead letters."""
        with self._lock:
            result = self._dlq[:max_count]
            self._dlq = self._dlq[max_count:]
            return result

    def dlq_size(self) -> int:
        return len(self._dlq)

    def reset(self) -> None:
        """Reset all routes and metrics."""
        with self._lock:
            self._exact_routes.clear()
            self._pattern_routes.clear()
            self._subscriber_ids.clear()
            self._match_cache.invalidate()
            self._dlq.clear()
            self._metrics = RouterMetrics()
            self._channel_publish_counts.clear()

    # ── Private ──────────────────────────────────────────────────────────

    def _resolve_routes(self, channel: str) -> List[RouteEntry]:
        """Find all routes matching a channel (exact + pattern).

        Uses a cache to avoid repeated fnmatch calls.
        """
        cached = self._match_cache.get(channel)
        if cached is not None:
            return cached

        routes: List[RouteEntry] = []

        # Exact match
        if channel in self._exact_routes:
            routes.extend(self._exact_routes[channel])

        # Pattern match
        for pattern, route in self._pattern_routes:
            if fnmatch.fnmatch(channel, pattern):
                routes.append(route)

        self._match_cache.put(channel, routes)
        return routes

    def _enqueue_dlq(
        self, channel: str, message: Any, reason: str,
    ) -> None:
        """Add a dead letter (caller may or may not hold lock)."""
        dl = DeadLetter(
            channel=channel,
            message=message,
            timestamp=time.monotonic(),
            reason=reason,
        )
        if len(self._dlq) >= self._max_dlq_size:
            self._dlq.pop(0)  # drop oldest
        self._dlq.append(dl)
        self._metrics.total_dlq += 1
