"""
BoundedQueue — Fixed-capacity FIFO with configurable overflow policy.
======================================================================

Apollo reference: ``cyber/base/bounded_queue.h``

Apollo's BoundedQueue is a lock-free ring buffer used as the backbone
for channel message passing between components.  In Python we use a
threading.Lock + collections.deque for correctness; the GIL already
provides basic atomicity but explicit locking ensures safety under
all Python implementations.

Design notes:
    - ``enqueue()`` returns False (not raises) when full — callers decide
      whether to drop or block, matching Apollo's ``Enqueue`` return code.
    - ``WaitEnqueue`` blocks until space is available (with timeout).
    - ``dequeue()`` returns None when empty — Apollo's ``Dequeue``.
    - Zero external dependencies; uses only stdlib.

Usage::

    q = BoundedQueue[GameSnapshot](capacity=128)
    ok = q.enqueue(snapshot)          # non-blocking
    q.wait_enqueue(snapshot, 0.5)     # blocks up to 0.5s
    item = q.dequeue()                # returns None if empty

Claude27: New file. Fills Apollo cyber/base/ gap.
Location: lolbot-HyperAI/cyber/base/bounded_queue.py
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Generic, List, Optional, TypeVar

T = TypeVar("T")

# Overflow policies matching Apollo's WaitStrategy variants.
OVERFLOW_DROP_OLDEST = "drop_oldest"
OVERFLOW_DROP_NEWEST = "drop_newest"
OVERFLOW_BLOCK = "block"


@dataclass
class BoundedQueueStats:
    """Runtime statistics for a BoundedQueue instance."""
    enqueue_count: int = 0
    dequeue_count: int = 0
    drop_count: int = 0
    block_count: int = 0
    peak_size: int = 0


class BoundedQueue(Generic[T]):
    """Fixed-capacity thread-safe FIFO queue.

    Apollo equivalent: ``cyber::base::BoundedQueue<T>``

    Args:
        capacity: Maximum number of elements. Must be > 0.
        overflow: What to do when queue is full:
            - ``"drop_oldest"``: discard head, enqueue at tail (default)
            - ``"drop_newest"``: reject the new element
            - ``"block"``: block caller until space is available
    """

    __slots__ = (
        "_capacity", "_overflow", "_buf", "_lock",
        "_not_full", "_not_empty", "stats",
    )

    def __init__(
        self,
        capacity: int = 128,
        overflow: str = OVERFLOW_DROP_OLDEST,
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        if overflow not in (
            OVERFLOW_DROP_OLDEST, OVERFLOW_DROP_NEWEST, OVERFLOW_BLOCK,
        ):
            raise ValueError(f"Unknown overflow policy: {overflow!r}")

        self._capacity = capacity
        self._overflow = overflow
        self._buf: Deque[T] = deque()
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self.stats = BoundedQueueStats()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    @property
    def empty(self) -> bool:
        with self._lock:
            return len(self._buf) == 0

    @property
    def full(self) -> bool:
        with self._lock:
            return len(self._buf) >= self._capacity

    def enqueue(self, item: T) -> bool:
        """Non-blocking enqueue. Returns True if accepted."""
        with self._lock:
            self.stats.enqueue_count += 1
            if len(self._buf) >= self._capacity:
                if self._overflow == OVERFLOW_DROP_OLDEST:
                    self._buf.popleft()
                    self.stats.drop_count += 1
                elif self._overflow == OVERFLOW_DROP_NEWEST:
                    self.stats.drop_count += 1
                    return False
                else:
                    raise ValueError("Use wait_enqueue() with block overflow")
            self._buf.append(item)
            if len(self._buf) > self.stats.peak_size:
                self.stats.peak_size = len(self._buf)
            self._not_empty.notify()
            return True

    def wait_enqueue(self, item: T, timeout: Optional[float] = None) -> bool:
        """Blocking enqueue — waits until space is available."""
        with self._not_full:
            self.stats.enqueue_count += 1
            while len(self._buf) >= self._capacity:
                self.stats.block_count += 1
                if not self._not_full.wait(timeout=timeout):
                    return False
            self._buf.append(item)
            if len(self._buf) > self.stats.peak_size:
                self.stats.peak_size = len(self._buf)
            self._not_empty.notify()
            return True

    def dequeue(self) -> Optional[T]:
        """Non-blocking dequeue. Returns None if empty."""
        with self._lock:
            if not self._buf:
                return None
            item = self._buf.popleft()
            self.stats.dequeue_count += 1
            self._not_full.notify()
            return item

    def wait_dequeue(self, timeout: Optional[float] = None) -> Optional[T]:
        """Blocking dequeue — waits until an item is available."""
        with self._not_empty:
            while not self._buf:
                if not self._not_empty.wait(timeout=timeout):
                    return None
            item = self._buf.popleft()
            self.stats.dequeue_count += 1
            self._not_full.notify()
            return item

    def peek(self) -> Optional[T]:
        """Peek at the head element without removing it."""
        with self._lock:
            return self._buf[0] if self._buf else None

    def peek_latest(self) -> Optional[T]:
        """Peek at the tail (most recently enqueued)."""
        with self._lock:
            return self._buf[-1] if self._buf else None

    def drain(self, max_items: int = 0) -> List[T]:
        """Drain up to max_items elements (0 = drain all)."""
        with self._lock:
            if max_items <= 0:
                max_items = len(self._buf)
            result: List[T] = []
            for _ in range(min(max_items, len(self._buf))):
                result.append(self._buf.popleft())
                self.stats.dequeue_count += 1
            if result:
                self._not_full.notify_all()
            return result

    def clear(self) -> int:
        """Remove all elements. Returns count removed."""
        with self._lock:
            count = len(self._buf)
            self._buf.clear()
            self.stats.drop_count += count
            self._not_full.notify_all()
            return count

    def snapshot(self) -> dict:
        """Return serializable status snapshot."""
        with self._lock:
            return {
                "capacity": self._capacity,
                "size": len(self._buf),
                "overflow": self._overflow,
                "stats": {
                    "enqueue_count": self.stats.enqueue_count,
                    "dequeue_count": self.stats.dequeue_count,
                    "drop_count": self.stats.drop_count,
                    "block_count": self.stats.block_count,
                    "peak_size": self.stats.peak_size,
                },
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"<BoundedQueue capacity={self._capacity} "
                f"size={len(self._buf)} overflow={self._overflow!r}>"
            )
