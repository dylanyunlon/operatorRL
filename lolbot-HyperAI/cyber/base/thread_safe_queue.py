"""
ThreadSafeQueue — Unbounded thread-safe FIFO queue.
=====================================================

Apollo reference: ``cyber/base/thread_safe_queue.h``

Unlike BoundedQueue, this has no capacity limit. Used internally by
scheduler and blocker for task queuing.

Claude27: New file. Fills Apollo cyber/base/ gap.
Location: lolbot-HyperAI/cyber/base/thread_safe_queue.py
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Generic, List, Optional, TypeVar

T = TypeVar("T")


class ThreadSafeQueue(Generic[T]):
    """Unbounded thread-safe FIFO queue.

    Apollo equivalent: ``cyber::base::ThreadSafeQueue<T>``
    """

    __slots__ = ("_buf", "_lock", "_not_empty", "_enqueue_count", "_dequeue_count")

    def __init__(self) -> None:
        self._buf: Deque[T] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._enqueue_count = 0
        self._dequeue_count = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    @property
    def empty(self) -> bool:
        with self._lock:
            return len(self._buf) == 0

    def enqueue(self, item: T) -> None:
        """Enqueue an item (never blocks, never fails)."""
        with self._lock:
            self._buf.append(item)
            self._enqueue_count += 1
            self._not_empty.notify()

    def dequeue(self) -> Optional[T]:
        """Non-blocking dequeue. Returns None if empty."""
        with self._lock:
            if not self._buf:
                return None
            self._dequeue_count += 1
            return self._buf.popleft()

    def wait_dequeue(self, timeout: Optional[float] = None) -> Optional[T]:
        """Blocking dequeue — waits until item available or timeout."""
        with self._not_empty:
            while not self._buf:
                if not self._not_empty.wait(timeout=timeout):
                    return None
            self._dequeue_count += 1
            return self._buf.popleft()

    def peek(self) -> Optional[T]:
        with self._lock:
            return self._buf[0] if self._buf else None

    def drain(self, max_items: int = 0) -> List[T]:
        """Drain up to max_items elements (0 = all)."""
        with self._lock:
            if max_items <= 0:
                max_items = len(self._buf)
            result: List[T] = []
            for _ in range(min(max_items, len(self._buf))):
                result.append(self._buf.popleft())
                self._dequeue_count += 1
            return result

    def clear(self) -> int:
        with self._lock:
            count = len(self._buf)
            self._buf.clear()
            return count

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "size": len(self._buf),
                "enqueue_count": self._enqueue_count,
                "dequeue_count": self._dequeue_count,
            }

    def __repr__(self) -> str:
        with self._lock:
            return f"<ThreadSafeQueue size={len(self._buf)}>"
