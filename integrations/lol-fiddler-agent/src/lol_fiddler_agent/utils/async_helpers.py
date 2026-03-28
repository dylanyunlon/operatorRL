"""
Async Helpers - Utility functions for async/await patterns.

Provides retry decorators, timeout wrappers, and task management
utilities commonly needed in async game monitoring code.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Coroutine, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    coro_factory: Callable[..., Coroutine[Any, Any, T]],
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs: Any,
) -> T:
    """Retry an async function with exponential backoff.

    Args:
        coro_factory: Async function to call
        max_retries: Maximum retry attempts
        delay: Initial delay between retries
        backoff: Backoff multiplier
        exceptions: Exception types to catch
    """
    last_error: Optional[Exception] = None
    current_delay = delay

    for attempt in range(max_retries + 1):
        try:
            return await coro_factory(**kwargs)
        except exceptions as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    "Retry %d/%d after %s (delay=%.1fs)",
                    attempt + 1, max_retries, e, current_delay,
                )
                await asyncio.sleep(current_delay)
                current_delay *= backoff

    raise last_error or RuntimeError("Retry failed with no exception")


async def timeout_async(
    coro: Coroutine[Any, Any, T],
    timeout: float = 10.0,
    default: Optional[T] = None,
) -> Optional[T]:
    """Run a coroutine with a timeout, returning default on timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Operation timed out after %.1fs", timeout)
        return default


class TaskGroup:
    """Manages a group of background tasks with error handling.

    Example::

        async with TaskGroup() as group:
            group.create_task(monitor_loop())
            group.create_task(heartbeat_loop())
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._errors: list[Exception] = []

    async def __aenter__(self) -> "TaskGroup":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.cancel_all()

    def create_task(
        self,
        coro: Coroutine[Any, Any, Any],
        name: Optional[str] = None,
    ) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(self._task_done)
        self._tasks.append(task)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        try:
            exc = task.exception()
            if exc:
                self._errors.append(exc)
                logger.error("Task %s failed: %s", task.get_name(), exc)
        except asyncio.CancelledError:
            pass

    async def cancel_all(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks if not t.done())

    @property
    def errors(self) -> list[Exception]:
        return list(self._errors)


class PeriodicTask:
    """Runs a coroutine at a fixed interval.

    Example::

        async def poll():
            data = await fetch_data()
            process(data)

        task = PeriodicTask(poll, interval=2.0)
        await task.start()
        ...
        await task.stop()
    """

    def __init__(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, None]],
        interval: float = 1.0,
        name: str = "periodic",
    ) -> None:
        self._factory = coro_factory
        self._interval = interval
        self._name = name
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._iteration_count = 0
        self._error_count = 0

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=self._name)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._factory()
                self._iteration_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                logger.error("Periodic task %s error: %s", self._name, e)
            await asyncio.sleep(self._interval)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "running": self._running,
            "iterations": self._iteration_count,
            "errors": self._error_count,
            "interval": self._interval,
        }
