"""
ThreadPool — Managed worker pool for async task execution.
=============================================================

Apollo reference: ``cyber/base/thread_pool.h``

Claude27: New file. Fills Apollo cyber/base/ gap.
Location: lolbot-HyperAI/cyber/base/thread_pool.py
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ThreadPoolStats:
    """Runtime statistics for a ThreadPool."""
    tasks_submitted: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_timed_out: int = 0
    peak_pending: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def on_submit(self) -> None:
        with self._lock:
            self.tasks_submitted += 1

    def on_complete(self, success: bool) -> None:
        with self._lock:
            if success:
                self.tasks_completed += 1
            else:
                self.tasks_failed += 1

    def on_timeout(self) -> None:
        with self._lock:
            self.tasks_timed_out += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "tasks_submitted": self.tasks_submitted,
                "tasks_completed": self.tasks_completed,
                "tasks_failed": self.tasks_failed,
                "tasks_timed_out": self.tasks_timed_out,
                "peak_pending": self.peak_pending,
            }


class ThreadPool:
    """Managed thread pool for async task execution.

    Apollo equivalent: ``cyber::base::ThreadPool``

    Args:
        max_workers: Maximum number of worker threads.
        name: Name prefix for worker threads.
    """

    def __init__(
        self,
        max_workers: int = 4,
        name: str = "cyber-pool",
    ) -> None:
        if max_workers <= 0:
            raise ValueError(f"max_workers must be > 0, got {max_workers}")
        self._max_workers = max_workers
        self._name = name
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._lock = threading.Lock()
        self._shutdown_flag = False
        self._pending_count = 0
        self.stats = ThreadPoolStats()

    @property
    def name(self) -> str:
        return self._name

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown_flag

    @property
    def pending_count(self) -> int:
        return self._pending_count

    def execute(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any,
    ) -> concurrent.futures.Future:
        """Submit a task for async execution. Returns a Future."""
        if self._shutdown_flag:
            raise RuntimeError(f"ThreadPool {self._name!r} is shut down")
        self._ensure_started()
        self.stats.on_submit()
        with self._lock:
            self._pending_count += 1
            if self._pending_count > self.stats.peak_pending:
                self.stats.peak_pending = self._pending_count
        return self._executor.submit(self._wrapped, fn, *args, **kwargs)

    def execute_with_timeout(
        self, fn: Callable[..., Any], timeout_s: float,
        *args: Any, **kwargs: Any,
    ) -> Optional[Any]:
        """Execute with timeout, blocking caller. Returns result or None."""
        future = self.execute(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            self.stats.on_timeout()
            logger.warning("[%s] Task timed out: %s", self._name, fn.__name__)
            return None

    def _ensure_started(self) -> None:
        if self._executor is not None:
            return
        with self._lock:
            if self._executor is not None:
                return
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix=self._name,
            )

    def _wrapped(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            result = fn(*args, **kwargs)
            self.stats.on_complete(success=True)
            return result
        except Exception as exc:
            self.stats.on_complete(success=False)
            logger.error("[%s] Task %s failed: %s", self._name, fn.__name__, exc)
            raise
        finally:
            with self._lock:
                self._pending_count = max(0, self._pending_count - 1)

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the pool."""
        with self._lock:
            if self._shutdown_flag:
                return
            self._shutdown_flag = True
        if self._executor is not None:
            self._executor.shutdown(wait=wait)

    def snapshot(self) -> dict:
        return {
            "name": self._name,
            "max_workers": self._max_workers,
            "is_shutdown": self._shutdown_flag,
            "pending_count": self._pending_count,
            "stats": self.stats.snapshot(),
        }

    def __repr__(self) -> str:
        return (
            f"<ThreadPool name={self._name!r} "
            f"workers={self._max_workers} "
            f"pending={self._pending_count}>"
        )
