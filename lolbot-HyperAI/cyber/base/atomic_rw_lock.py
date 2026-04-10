#!/usr/bin/env python3
"""
cyber/base/atomic_rw_lock.py — Atomic Read-Write Lock
=======================================================

从 Apollo `cyber/base/atomic_rw_lock.h` 这个好例子开始。然后, 遵循该模式实现
一个新的 `AtomicRWLock`, 让系统可以在读多写少的场景下高效同步。

Apollo reference:
    cyber/base/atomic_rw_lock.h   — AtomicRWLock class

位置: lolbot-HyperAI/cyber/base/atomic_rw_lock.py
"""

from __future__ import annotations

import contextlib
import threading
from typing import Generator


class AtomicRWLock:
    """
    Atomic read-write lock with writer priority.
    
    Apollo equivalent: cyber/base/atomic_rw_lock.h
    
    Features:
    - Multiple readers can hold the lock simultaneously
    - Writers have exclusive access
    - Writer priority to prevent starvation
    
    Usage::
    
        lock = AtomicRWLock()
        
        # Read lock
        with lock.read_lock():
            data = shared_data
        
        # Write lock
        with lock.write_lock():
            shared_data = new_value
    """
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._readers_ok = threading.Condition(self._lock)
        self._writers_ok = threading.Condition(self._lock)
        
        self._reader_count = 0
        self._writer_count = 0
        self._waiting_writers = 0
    
    def acquire_read(self) -> None:
        """Acquire read lock."""
        with self._lock:
            # Wait if there are writers or waiting writers
            while self._writer_count > 0 or self._waiting_writers > 0:
                self._readers_ok.wait()
            self._reader_count += 1
    
    def release_read(self) -> None:
        """Release read lock."""
        with self._lock:
            self._reader_count -= 1
            if self._reader_count == 0:
                self._writers_ok.notify()
    
    def acquire_write(self) -> None:
        """Acquire write lock."""
        with self._lock:
            self._waiting_writers += 1
            # Wait until no readers or writers
            while self._reader_count > 0 or self._writer_count > 0:
                self._writers_ok.wait()
            self._waiting_writers -= 1
            self._writer_count += 1
    
    def release_write(self) -> None:
        """Release write lock."""
        with self._lock:
            self._writer_count -= 1
            if self._waiting_writers > 0:
                # Wake one waiting writer
                self._writers_ok.notify()
            else:
                # Wake all waiting readers
                self._readers_ok.notify_all()
    
    @contextlib.contextmanager
    def read_lock(self) -> Generator[None, None, None]:
        """Context manager for read lock."""
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()
    
    @contextlib.contextmanager
    def write_lock(self) -> Generator[None, None, None]:
        """Context manager for write lock."""
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()
    
    @property
    def reader_count(self) -> int:
        """Number of active readers."""
        with self._lock:
            return self._reader_count
    
    @property
    def writer_count(self) -> int:
        """Number of active writers (0 or 1)."""
        with self._lock:
            return self._writer_count


class ReadLockGuard:
    """RAII-style read lock guard (alternative to context manager)."""
    
    def __init__(self, lock: AtomicRWLock) -> None:
        self._lock = lock
        self._lock.acquire_read()
    
    def __del__(self) -> None:
        self._lock.release_read()


class WriteLockGuard:
    """RAII-style write lock guard (alternative to context manager)."""
    
    def __init__(self, lock: AtomicRWLock) -> None:
        self._lock = lock
        self._lock.acquire_write()
    
    def __del__(self) -> None:
        self._lock.release_write()
