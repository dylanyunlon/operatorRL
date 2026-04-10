#!/usr/bin/env python3
"""
cyber/base/wait_strategy.py — Wait Strategies
===============================================

从 Apollo `cyber/base/wait_strategy.h` 这个好例子开始。然后, 遵循该模式实现
多种等待策略, 让系统可以在不同场景下选择最优的等待方式。

Apollo reference:
    cyber/base/wait_strategy.h   — BlockWaitStrategy, SleepWaitStrategy, etc.

位置: lolbot-HyperAI/cyber/base/wait_strategy.py
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


class WaitStrategy(ABC):
    """
    Abstract base class for wait strategies.
    
    Apollo equivalent: cyber/base/wait_strategy.h
    
    Wait strategies control how threads wait for conditions.
    Different strategies trade off CPU usage vs latency.
    """
    
    @abstractmethod
    def notify_one(self) -> None:
        """Wake one waiting thread."""
        pass
    
    @abstractmethod
    def notify_all(self) -> None:
        """Wake all waiting threads."""
        pass
    
    @abstractmethod
    def wait(self) -> None:
        """Wait indefinitely."""
        pass
    
    @abstractmethod
    def wait_for(self, timeout_ms: float) -> bool:
        """Wait with timeout.
        
        Returns:
            True if notified, False if timeout
        """
        pass


class BlockWaitStrategy(WaitStrategy):
    """
    Blocking wait strategy using condition variable.
    
    Apollo equivalent: BlockWaitStrategy
    
    - Low CPU usage
    - Higher latency (kernel transition)
    - Best for: low-frequency events, batch processing
    """
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
    
    def notify_one(self) -> None:
        with self._condition:
            self._condition.notify()
    
    def notify_all(self) -> None:
        with self._condition:
            self._condition.notify_all()
    
    def wait(self) -> None:
        with self._condition:
            self._condition.wait()
    
    def wait_for(self, timeout_ms: float) -> bool:
        with self._condition:
            return self._condition.wait(timeout=timeout_ms / 1000.0)


class SleepWaitStrategy(WaitStrategy):
    """
    Sleep-based wait strategy.
    
    Apollo equivalent: SleepWaitStrategy
    
    - Moderate CPU usage
    - Moderate latency
    - Best for: medium-frequency polling
    """
    
    def __init__(self, sleep_ms: float = 1.0) -> None:
        self._sleep_s = sleep_ms / 1000.0
        self._notified = threading.Event()
    
    def notify_one(self) -> None:
        self._notified.set()
    
    def notify_all(self) -> None:
        self._notified.set()
    
    def wait(self) -> None:
        while not self._notified.is_set():
            time.sleep(self._sleep_s)
        self._notified.clear()
    
    def wait_for(self, timeout_ms: float) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while not self._notified.is_set():
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(self._sleep_s, deadline - time.monotonic()))
        self._notified.clear()
        return True


class YieldWaitStrategy(WaitStrategy):
    """
    Yield-based wait strategy (busy wait with yield).
    
    Apollo equivalent: YieldWaitStrategy
    
    - Higher CPU usage
    - Lower latency
    - Best for: high-frequency, low-latency requirements
    """
    
    def __init__(self) -> None:
        self._notified = threading.Event()
    
    def notify_one(self) -> None:
        self._notified.set()
    
    def notify_all(self) -> None:
        self._notified.set()
    
    def wait(self) -> None:
        while not self._notified.is_set():
            # Yield to other threads
            time.sleep(0)
        self._notified.clear()
    
    def wait_for(self, timeout_ms: float) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while not self._notified.is_set():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0)
        self._notified.clear()
        return True


class BusySpinWaitStrategy(WaitStrategy):
    """
    Busy spin wait strategy (pure busy wait).
    
    Apollo equivalent: BusySpinWaitStrategy
    
    - Highest CPU usage
    - Lowest latency
    - Best for: ultra-low-latency, dedicated cores
    
    WARNING: Uses 100% CPU while waiting!
    """
    
    def __init__(self) -> None:
        self._notified = threading.Event()
    
    def notify_one(self) -> None:
        self._notified.set()
    
    def notify_all(self) -> None:
        self._notified.set()
    
    def wait(self) -> None:
        while not self._notified.is_set():
            pass  # Pure spin
        self._notified.clear()
    
    def wait_for(self, timeout_ms: float) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while not self._notified.is_set():
            if time.monotonic() >= deadline:
                return False
        self._notified.clear()
        return True


class TimeoutBlockWaitStrategy(WaitStrategy):
    """
    Block strategy with automatic timeout.
    
    Combines blocking with periodic wakeups for timeout checks.
    """
    
    def __init__(self, check_interval_ms: float = 100.0) -> None:
        self._check_interval_s = check_interval_ms / 1000.0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
    
    def notify_one(self) -> None:
        with self._condition:
            self._condition.notify()
    
    def notify_all(self) -> None:
        with self._condition:
            self._condition.notify_all()
    
    def wait(self) -> None:
        with self._condition:
            self._condition.wait()
    
    def wait_for(self, timeout_ms: float) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000.0
        with self._condition:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                
                wait_time = min(remaining, self._check_interval_s)
                if self._condition.wait(timeout=wait_time):
                    return True


@dataclass
class WaitStrategyFactory:
    """Factory for creating wait strategies."""
    
    @staticmethod
    def create(strategy_type: str, **kwargs) -> WaitStrategy:
        """Create a wait strategy by type name.
        
        Args:
            strategy_type: One of "block", "sleep", "yield", "spin", "timeout_block"
            **kwargs: Strategy-specific arguments
        
        Returns:
            The created strategy
        """
        strategies = {
            "block": BlockWaitStrategy,
            "sleep": SleepWaitStrategy,
            "yield": YieldWaitStrategy,
            "spin": BusySpinWaitStrategy,
            "timeout_block": TimeoutBlockWaitStrategy,
        }
        
        if strategy_type not in strategies:
            raise ValueError(f"Unknown strategy type: {strategy_type}")
        
        return strategies[strategy_type](**kwargs)
