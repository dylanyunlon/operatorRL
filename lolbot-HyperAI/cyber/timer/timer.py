#!/usr/bin/env python3
"""
cyber/timer/timer.py — Apollo-style Timer High-Level API
==========================================================

从 Apollo `cyber/timer/timer.cc` 这个好例子开始。然后, 遵循该模式实现
一个新的 `Timer`, 作为 TimingWheel 的高级封装, 提供简单易用的定时API。

Apollo reference:
    cyber/timer/timer.cc   — Timer::Start/Stop/SetTimerOption
    cyber/timer/timer.h    — TimerOption struct

位置: lolbot-HyperAI/cyber/timer/timer.py

Claude29: New file — fills gap vs Apollo Timer API.
         Based on Claude27-28 timer infrastructure, pure addition.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from cyber.timer.timing_wheel import TimingWheel


@dataclass
class TimerOption:
    """
    Timer configuration options.
    
    Apollo equivalent: cyber/timer/timer.h TimerOption struct
    
    Attributes:
        period: Timer period in milliseconds (1 to 32768)
        callback: Function to execute when timer fires
        oneshot: If True, timer fires once; if False, repeats
    """
    period: int = 100
    callback: Optional[Callable[[], None]] = None
    oneshot: bool = False
    
    def __post_init__(self):
        # Apollo limits: max = 512 * 64 = 32768ms, min = 1ms
        if self.period < 1:
            self.period = 1
        elif self.period > 32768:
            self.period = 32768


class Timer:
    """
    High-level timer using the TimingWheel.
    
    Apollo equivalent: cyber/timer/timer.cc
    
    This class wraps the TimingWheel to provide a simple, user-friendly
    timer interface. Multiple Timer instances share the same TimingWheel
    singleton for efficiency.
    
    Usage::
    
        # One-shot timer (fires once after 1000ms)
        timer = Timer(1000, my_callback, oneshot=True)
        timer.start()
        
        # Periodic timer (fires every 100ms)
        timer = Timer(100, my_callback, oneshot=False)
        timer.start()
        timer.stop()  # Stop when done
        
        # Using TimerOption
        opt = TimerOption(period=500, callback=my_func, oneshot=False)
        timer = Timer(option=opt)
        timer.start()
    
    Notes:
        - Start() adds the task to the shared TimingWheel
        - Stop() removes the task from the wheel
        - The TimingWheel starts automatically when first Timer.Start() is called
        - Thread-safe: multiple threads can create/manage timers
    """
    
    _timer_id_counter = 0
    _timer_id_lock = threading.Lock()
    
    def __init__(
        self,
        period: int = 0,
        callback: Optional[Callable[[], None]] = None,
        oneshot: bool = False,
        option: Optional[TimerOption] = None,
    ) -> None:
        """Create a Timer.
        
        Apollo equivalent: Timer::Timer()
        
        Args:
            period: Timer period in milliseconds
            callback: Function to execute
            oneshot: One-shot or periodic
            option: TimerOption to use (overrides other args)
        """
        if option is not None:
            self._option = option
        else:
            self._option = TimerOption(
                period=period,
                callback=callback,
                oneshot=oneshot,
            )
        
        # Assign unique timer ID
        with self._timer_id_lock:
            Timer._timer_id_counter += 1
            self._timer_id = Timer._timer_id_counter
        
        self._task_id: Optional[int] = None
        self._started = False
        self._lock = threading.Lock()
        
        # Get shared timing wheel
        self._timing_wheel = TimingWheel.instance()
    
    def set_timer_option(self, option: TimerOption) -> None:
        """Set timer option.
        
        Apollo equivalent: Timer::SetTimerOption()
        
        Can only be called before Start().
        """
        with self._lock:
            if self._started:
                raise RuntimeError("Cannot change option while timer is running")
            self._option = option
    
    def start(self) -> bool:
        """Start the timer.
        
        Apollo equivalent: Timer::Start()
        
        Returns:
            True if timer was started successfully
        """
        with self._lock:
            if self._started:
                return True
            
            if self._option.callback is None:
                return False
            
            # Ensure timing wheel is running
            if not self._timing_wheel.is_running:
                self._timing_wheel.start()
            
            # Add task to wheel
            self._task_id = self._timing_wheel.add_task(
                callback=self._option.callback,
                interval_ms=float(self._option.period),
                oneshot=self._option.oneshot,
                name=f"timer_{self._timer_id}",
            )
            
            self._started = True
            return True
    
    def stop(self) -> None:
        """Stop the timer.
        
        Apollo equivalent: Timer::Stop()
        """
        with self._lock:
            if not self._started:
                return
            
            if self._task_id is not None:
                self._timing_wheel.remove_task(self._task_id)
                self._task_id = None
            
            self._started = False
    
    @property
    def is_running(self) -> bool:
        """Check if timer is running."""
        with self._lock:
            return self._started
    
    @property
    def timer_id(self) -> int:
        """Get unique timer ID."""
        return self._timer_id
    
    @property
    def period(self) -> int:
        """Get timer period in ms."""
        return self._option.period
    
    def __del__(self):
        """Cleanup on destruction."""
        self.stop()
    
    def __repr__(self) -> str:
        return (
            f"Timer(id={self._timer_id}, period={self._option.period}ms, "
            f"oneshot={self._option.oneshot}, running={self._started})"
        )


# ─── Convenience Functions ─────────────────────────────────────────────────

def create_timer(
    period_ms: int,
    callback: Callable[[], None],
    oneshot: bool = False,
    auto_start: bool = True,
) -> Timer:
    """Create and optionally start a timer.
    
    Convenience function for common timer creation pattern.
    
    Args:
        period_ms: Timer period in milliseconds
        callback: Function to call when timer fires
        oneshot: If True, timer fires once
        auto_start: If True, start immediately
    
    Returns:
        The created Timer
    """
    timer = Timer(period_ms, callback, oneshot)
    if auto_start:
        timer.start()
    return timer


def schedule_once(delay_ms: int, callback: Callable[[], None]) -> Timer:
    """Schedule a one-shot callback.
    
    Args:
        delay_ms: Delay before callback in milliseconds
        callback: Function to call
    
    Returns:
        The created Timer (can be used to cancel)
    """
    return create_timer(delay_ms, callback, oneshot=True, auto_start=True)


def schedule_periodic(period_ms: int, callback: Callable[[], None]) -> Timer:
    """Schedule a periodic callback.
    
    Args:
        period_ms: Period between callbacks in milliseconds
        callback: Function to call
    
    Returns:
        The created Timer (use timer.stop() to cancel)
    """
    return create_timer(period_ms, callback, oneshot=False, auto_start=True)
