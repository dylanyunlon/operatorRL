"""
cyber/timer — Apollo-style Timing Wheel Implementation
=======================================================

This module provides a high-precision O(1) timing wheel for scheduling
periodic and one-shot timer tasks.

Apollo reference: cyber/timer/

Claude30: Added ProcWatchdog for Proc() execution monitoring

Usage::

    from cyber.timer import TimingWheel, TimerTask
    
    wheel = TimingWheel.instance()
    wheel.start()
    
    task_id = wheel.add_task(my_callback, interval_ms=100)
    
    wheel.stop()
"""

from cyber.timer.timer_task import TimerTask, TaskState
from cyber.timer.timer_bucket import TimerBucket
from cyber.timer.timing_wheel import TimingWheel, TimingWheelConfig, WheelState
# Claude30: Proc() watchdog
from cyber.timer.watchdog import (
    ProcWatchdog,
    WatchdogConfig,
    WatchdogManager,
    WatchdogState,
)

__all__ = [
    "TimerTask",
    "TaskState",
    "TimerBucket",
    "TimingWheel",
    "TimingWheelConfig",
    "WheelState",
    # Claude30
    "ProcWatchdog",
    "WatchdogConfig",
    "WatchdogManager",
    "WatchdogState",
]
