"""
Session Manager - Manages lifecycle of captured Fiddler HTTP sessions.

Provides a sliding-window buffer of recent sessions, automatic eviction
of stale data, and change-detection callbacks for game state transitions.

Memory model:
  - Fixed-size ring buffer (default 4096 entries) prevents OOM during
    long monitoring sessions.
  - "Hot" sessions (last 60 s) are indexed by category for O(1) lookup.
  - "Cold" sessions are evicted but their signatures are retained for
    dedup (compact ~64 bytes each vs ~2 KB full session).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from lol_fiddler_agent.network.fiddler_client import FiddlerMCPClient, FilterCriteria, HTTPSession
from lol_fiddler_agent.network.packet_analyzer import (
    AnalyzedPacket,
    APIEndpointCategory,
    GameLifecyclePhase,
    PacketAnalyzer,
)

logger = logging.getLogger(__name__)

# Type alias for async callbacks
StateChangeCallback = Callable[[GameLifecyclePhase, GameLifecyclePhase], Coroutine[Any, Any, None]]
PacketCallback = Callable[[AnalyzedPacket], Coroutine[Any, Any, None]]


@dataclass
class SessionManagerConfig:
    """Configuration for SessionManager."""
    buffer_size: int = 4096
    hot_window_seconds: float = 60.0
    poll_interval: float = 1.0
    auto_clear_on_game_end: bool = True
    retain_signatures_count: int = 8192


class SessionRingBuffer:
    """Fixed-size ring buffer for analyzed packets.

    When full, oldest entries are evicted silently.
    Provides O(1) append and O(n) iteration (n = current size).
    """

    def __init__(self, capacity: int = 4096) -> None:
        self._capacity = max(capacity, 16)
        self._buffer: deque[AnalyzedPacket] = deque(maxlen=self._capacity)
        self._total_added: int = 0
        self._total_evicted: int = 0

    def append(self, packet: AnalyzedPacket) -> Optional[AnalyzedPacket]:
        """Append a packet, returning the evicted packet if buffer was full."""
        evicted: Optional[AnalyzedPacket] = None
        if len(self._buffer) >= self._capacity:
            evicted = self._buffer[0]
            self._total_evicted += 1
        self._buffer.append(packet)
        self._total_added += 1
        return evicted

    def get_recent(self, seconds: float = 60.0) -> list[AnalyzedPacket]:
        """Get packets from the last N seconds."""
        cutoff = time.time() - seconds
        result: list[AnalyzedPacket] = []
        # Iterate from newest to oldest
        for pkt in reversed(self._buffer):
            ts = pkt.session.start_time
            if ts and ts.timestamp() < cutoff:
                break
            result.append(pkt)
        result.reverse()
        return result

    def get_by_category(
        self, category: APIEndpointCategory, limit: int = 50,
    ) -> list[AnalyzedPacket]:
        """Get most recent packets of a given category."""
        result: list[AnalyzedPacket] = []
        for pkt in reversed(self._buffer):
            if pkt.category == category:
                result.append(pkt)
                if len(result) >= limit:
                    break
        result.reverse()
        return result

    def latest(self) -> Optional[AnalyzedPacket]:
        """Get the most recently added packet."""
        if self._buffer:
            return self._buffer[-1]
        return None

    def clear(self) -> int:
        """Clear the buffer and return number of items removed."""
        count = len(self._buffer)
        self._buffer.clear()
        return count

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def total_added(self) -> int:
        return self._total_added

    @property
    def total_evicted(self) -> int:
        return self._total_evicted

    def __len__(self) -> int:
        return len(self._buffer)

    def __iter__(self):
        return iter(self._buffer)


class SessionManager:
    """Manages the capture-analyze-dispatch pipeline.

    Polls the FiddlerMCPClient at a configurable interval, routes
    analyzed packets through registered callbacks, and maintains
    lifecycle state transitions.

    Example::

        manager = SessionManager(fiddler_client, config)
        manager.on_game_state_change(my_lifecycle_handler)
        manager.on_live_client_data(my_game_data_handler)
        await manager.start()
        ...
        await manager.stop()
    """

    def __init__(
        self,
        fiddler: FiddlerMCPClient,
        config: Optional[SessionManagerConfig] = None,
    ) -> None:
        self._fiddler = fiddler
        self._config = config or SessionManagerConfig()
        self._analyzer = PacketAnalyzer(dedup_window=5.0)
        self._buffer = SessionRingBuffer(capacity=self._config.buffer_size)
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._last_session_id: int = 0

        # Callbacks
        self._state_change_callbacks: list[StateChangeCallback] = []
        self._packet_callbacks: list[PacketCallback] = []
        self._category_callbacks: dict[APIEndpointCategory, list[PacketCallback]] = {}

        # State
        self._current_phase = GameLifecyclePhase.IDLE
        self._phase_entered_at: float = time.time()

    # ── Callback Registration ─────────────────────────────────────────────

    def on_game_state_change(self, callback: StateChangeCallback) -> None:
        """Register a callback for game lifecycle phase changes."""
        self._state_change_callbacks.append(callback)

    def on_packet(self, callback: PacketCallback) -> None:
        """Register a callback for every analyzed (non-duplicate) packet."""
        self._packet_callbacks.append(callback)

    def on_category(
        self, category: APIEndpointCategory, callback: PacketCallback,
    ) -> None:
        """Register a callback for packets of a specific category."""
        if category not in self._category_callbacks:
            self._category_callbacks[category] = []
        self._category_callbacks[category].append(callback)

    def on_live_client_data(self, callback: PacketCallback) -> None:
        """Convenience: register for Live Client API allgamedata responses."""
        self.on_category(APIEndpointCategory.LIVE_CLIENT_ALL_GAME, callback)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start polling Fiddler for new sessions."""
        if self._running:
            logger.warning("SessionManager already running")
            return

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "SessionManager started (poll=%.1fs, buffer=%d)",
            self._config.poll_interval, self._config.buffer_size,
        )

    async def stop(self) -> None:
        """Stop polling."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info(
            "SessionManager stopped (processed=%d, evicted=%d)",
            self._buffer.total_added, self._buffer.total_evicted,
        )

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_phase(self) -> GameLifecyclePhase:
        return self._current_phase

    @property
    def buffer(self) -> SessionRingBuffer:
        return self._buffer

    # ── Poll Loop ─────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Poll error: %s", e, exc_info=True)
            await asyncio.sleep(self._config.poll_interval)

    async def _poll_once(self) -> None:
        """Single poll iteration: fetch new sessions, analyze, dispatch."""
        sessions = await self._fiddler.get_sessions(limit=100)

        # Filter to sessions we haven't seen yet
        new_sessions = [
            s for s in sessions
            if s.session_id > self._last_session_id
        ]
        if not new_sessions:
            return

        # Update watermark
        self._last_session_id = max(s.session_id for s in new_sessions)

        # Analyze and dispatch
        analyzed = self._analyzer.analyze_batch(new_sessions)
        for pkt in analyzed:
            self._buffer.append(pkt)
            if not pkt.is_duplicate:
                await self._dispatch(pkt)

    async def _dispatch(self, pkt: AnalyzedPacket) -> None:
        """Dispatch an analyzed packet to registered callbacks."""
        # Check for lifecycle transition
        if pkt.lifecycle_hint and pkt.lifecycle_hint != self._current_phase:
            old_phase = self._current_phase
            self._current_phase = pkt.lifecycle_hint
            self._phase_entered_at = time.time()
            await self._fire_state_change(old_phase, pkt.lifecycle_hint)

        # General packet callbacks
        for cb in self._packet_callbacks:
            try:
                await cb(pkt)
            except Exception as e:
                logger.warning("Packet callback error: %s", e)

        # Category-specific callbacks
        cat_cbs = self._category_callbacks.get(pkt.category, [])
        for cb in cat_cbs:
            try:
                await cb(pkt)
            except Exception as e:
                logger.warning("Category callback error: %s", e)

    async def _fire_state_change(
        self, old: GameLifecyclePhase, new: GameLifecyclePhase,
    ) -> None:
        """Fire all lifecycle change callbacks."""
        logger.info("Game lifecycle: %s → %s", old.value, new.value)
        for cb in self._state_change_callbacks:
            try:
                await cb(old, new)
            except Exception as e:
                logger.warning("State change callback error: %s", e)

        # Auto-clear on game end
        if (
            self._config.auto_clear_on_game_end
            and new == GameLifecyclePhase.POST_GAME
        ):
            logger.info("Auto-clearing Fiddler sessions (game ended)")
            try:
                await self._fiddler.clear_sessions()
            except Exception as e:
                logger.warning("Failed to auto-clear: %s", e)

    # ── Query Helpers ─────────────────────────────────────────────────────

    def get_latest_game_data(self) -> Optional[AnalyzedPacket]:
        """Get the most recent Live Client allgamedata packet."""
        recent = self._buffer.get_by_category(
            APIEndpointCategory.LIVE_CLIENT_ALL_GAME, limit=1,
        )
        return recent[0] if recent else None

    def get_statistics(self):
        """Proxy to analyzer statistics."""
        return self._analyzer.get_statistics()

    def get_phase_duration(self) -> float:
        """Seconds spent in the current lifecycle phase."""
        return time.time() - self._phase_entered_at
