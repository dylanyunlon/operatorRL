"""
cyber.base — Low-level concurrency primitives (Apollo parity).
================================================================

Maps Apollo ``cyber/base/`` to Python: bounded queues, thread pools,
signal/slot observer pattern, and thread-safe collections.

Apollo reference:
    cyber/base/bounded_queue.h      → bounded_queue.py
    cyber/base/thread_pool.h        → thread_pool.py
    cyber/base/signal.h             → signal.py
    cyber/base/thread_safe_queue.h  → thread_safe_queue.py

Claude27: New layer — fills structural gap vs Apollo.
"""

from cyber.base.bounded_queue import BoundedQueue
from cyber.base.thread_safe_queue import ThreadSafeQueue
from cyber.base.thread_pool import ThreadPool
from cyber.base.signal import Signal, Connection

__all__ = [
    "BoundedQueue",
    "ThreadSafeQueue",
    "ThreadPool",
    "Signal",
    "Connection",
]
