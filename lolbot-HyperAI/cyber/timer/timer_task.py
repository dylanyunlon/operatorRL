#!/usr/bin/env python3
"""
cyber/timer/timer_task.py — Timer Task Encapsulation
=====================================================

从 Apollo `cyber/timer/timer_task.h` 这个好例子开始。然后, 遵循该模式实现
一个新的 `TimerTask`, 让定时任务可以封装回调函数、间隔、执行计数等元数据。

Apollo reference:
    cyber/timer/timer_task.h   — TimerTask struct

位置: lolbot-HyperAI/cyber/timer/timer_task.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional


class TaskState(Enum):
    """Timer task state machine."""
    WAITING = auto()    # Waiting for execution
    RUNNING = auto()    # Currently executing
    COMPLETED = auto()  # Finished (one-shot)
    CANCELLED = auto()  # Cancelled by user


@dataclass
class TimerTask:
    """
    A single timer task in the timing wheel.
    
    Apollo equivalent: cyber/timer/timer_task.h
    
    Attributes:
        task_id: Unique task identifier
        callback: Function to execute when timer fires
        interval_ms: Interval between executions
        oneshot: If True, runs once then completes
        name: Human-readable name for debugging
        target_slot: Current slot in timing wheel
        state: Current task state
        execution_count: Number of times task has executed
        created_at: Timestamp when task was created
    """
    
    task_id: int
    callback: Callable[[], None]
    interval_ms: float
    oneshot: bool = False
    name: str = ""
    target_slot: int = 0
    state: TaskState = TaskState.WAITING
    execution_count: int = 0
    created_at: float = field(default_factory=time.monotonic)
    last_executed_at: float = 0.0
    
    def fire(self) -> bool:
        """Execute the task callback.
        
        Returns:
            True if execution succeeded
        """
        if self.state == TaskState.CANCELLED:
            return False
        
        self.state = TaskState.RUNNING
        self.last_executed_at = time.monotonic()
        
        try:
            self.callback()
            self.execution_count += 1
            return True
        except Exception:
            return False
        finally:
            if self.oneshot:
                self.state = TaskState.COMPLETED
            else:
                self.state = TaskState.WAITING
    
    def cancel(self) -> None:
        """Cancel this task."""
        self.state = TaskState.CANCELLED
    
    @property
    def is_active(self) -> bool:
        """Check if task is still active."""
        return self.state in (TaskState.WAITING, TaskState.RUNNING)
    
    @property
    def age_ms(self) -> float:
        """Time since task was created."""
        return (time.monotonic() - self.created_at) * 1000.0
    
    def __repr__(self) -> str:
        return (
            f"TimerTask(id={self.task_id}, name={self.name!r}, "
            f"interval={self.interval_ms}ms, state={self.state.name})"
        )
