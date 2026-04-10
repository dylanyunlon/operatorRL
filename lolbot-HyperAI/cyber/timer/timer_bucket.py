#!/usr/bin/env python3
"""
cyber/timer/timer_bucket.py — Timer Bucket for Timing Wheel
============================================================

从 Apollo `cyber/timer/timer_bucket.h` 这个好例子开始。然后, 遵循该模式实现
一个新的 `TimerBucket`, 让时间轮的每个槽位可以存储多个定时任务, 并能 O(1)
添加和批量取出任务。

Apollo reference:
    cyber/timer/timer_bucket.h   — TimerBucket class

位置: lolbot-HyperAI/cyber/timer/timer_bucket.py
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from cyber.timer.timer_task import TimerTask


@dataclass
class TimerBucket:
    """
    A bucket (slot) in the timing wheel that holds timer tasks.
    
    Apollo equivalent: cyber/timer/timer_bucket.h
    
    Each bucket represents a single tick slot in the timing wheel.
    Tasks with the same target tick are stored in the same bucket.
    
    Thread-safe: Uses a lock for concurrent access.
    """
    
    slot_index: int = 0
    _tasks: List[TimerTask] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def add(self, task: TimerTask) -> None:
        """Add a task to this bucket.
        
        O(1) time complexity.
        """
        with self._lock:
            self._tasks.append(task)
    
    def drain(self) -> List[TimerTask]:
        """Remove and return all tasks in this bucket.
        
        O(1) time complexity (list swap).
        
        Returns:
            List of all tasks that were in the bucket
        """
        with self._lock:
            tasks = self._tasks
            self._tasks = []
            return tasks
    
    def remove(self, task_id: int) -> bool:
        """Remove a specific task by ID.
        
        O(n) time complexity in worst case.
        
        Returns:
            True if task was found and removed
        """
        with self._lock:
            for i, task in enumerate(self._tasks):
                if task.task_id == task_id:
                    self._tasks.pop(i)
                    return True
            return False
    
    def clear(self) -> int:
        """Remove all tasks from bucket.
        
        Returns:
            Number of tasks removed
        """
        with self._lock:
            count = len(self._tasks)
            self._tasks.clear()
            return count
    
    @property
    def size(self) -> int:
        """Number of tasks in bucket."""
        with self._lock:
            return len(self._tasks)
    
    @property
    def is_empty(self) -> bool:
        """Check if bucket is empty."""
        with self._lock:
            return len(self._tasks) == 0
    
    def __len__(self) -> int:
        return self.size
    
    def __repr__(self) -> str:
        return f"TimerBucket(slot={self.slot_index}, tasks={self.size})"
