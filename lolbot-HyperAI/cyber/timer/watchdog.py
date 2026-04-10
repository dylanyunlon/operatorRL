"""
cyber/timer/watchdog.py — Proc() Loop Watchdog
================================================

Apollo reference:
    cyber/timer/timer.h        Timer::Start()
    cyber/timer/timing_wheel.h TimingWheel::Tick()

查看 Apollo cyber/timer 上现有 Timer 的实现方式，理解其模式，
特别是 **定时触发** 和 **超时检测** 是如何分离的。

从 Apollo `Timer::Start()` 这个好例子开始。然后，遵循该模式实现
一个新的 `ProcWatchdog`，让 Proc() 循环可以被监控超时，并能
在 Proc() 执行时间过长时触发告警。

Design notes:
    - Independent thread monitoring Proc() execution time
    - Configurable timeout threshold (default: 2x interval)
    - Callback on timeout for alerting/logging
    - Does NOT kill Proc() — only observes and reports

Claude30: Initial implementation
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional

from cyber.logger.cyber_logger import get_logger

logger = get_logger("cyber.timer.watchdog")


class WatchdogState(Enum):
    """Watchdog state."""
    IDLE = auto()
    WATCHING = auto()
    TIMEOUT = auto()
    STOPPED = auto()


@dataclass
class WatchdogConfig:
    """Watchdog configuration."""
    # Timeout threshold (seconds)
    # If Proc() doesn't complete within this time, trigger timeout
    timeout_s: float = 0.2  # 200ms default (2x 100ms interval)
    
    # Check interval (seconds)
    check_interval_s: float = 0.01  # 10ms
    
    # Maximum consecutive timeouts before escalation
    max_consecutive_timeouts: int = 10
    
    # Auto-restart after timeout
    auto_restart: bool = True


class ProcWatchdog:
    """Watchdog for monitoring Proc() execution.
    
    This is a safety mechanism to detect when Proc() takes too long.
    It runs in a separate thread and monitors the Proc() heartbeat.
    
    Usage::
    
        watchdog = ProcWatchdog("canbus", config)
        watchdog.register_timeout_callback(on_timeout)
        watchdog.start()
        
        # In Proc() loop:
        watchdog.begin_proc()
        try:
            # ... actual Proc() work ...
        finally:
            watchdog.end_proc()
        
        # On shutdown:
        watchdog.stop()
    """
    
    def __init__(
        self,
        component_name: str,
        config: Optional[WatchdogConfig] = None,
    ) -> None:
        self._name = component_name
        self._config = config or WatchdogConfig()
        
        self._state = WatchdogState.IDLE
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        
        # Proc() timing state
        self._proc_start_time: float = 0.0
        self._proc_in_progress: bool = False
        self._consecutive_timeouts: int = 0
        
        # Callbacks
        self._timeout_callback: Optional[Callable[[str, float], None]] = None
        self._recovery_callback: Optional[Callable[[str], None]] = None
        
        # Statistics
        self._stats = {
            "total_procs": 0,
            "total_timeouts": 0,
            "max_proc_time_ms": 0.0,
            "avg_proc_time_ms": 0.0,
            "last_timeout_time": 0.0,
        }
        self._proc_times: list = []  # rolling window for avg
        self._max_proc_times = 100
    
    def start(self) -> None:
        """Start the watchdog thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Watchdog %s already running", self._name)
            return
        
        self._stop_event.clear()
        self._state = WatchdogState.WATCHING
        self._thread = threading.Thread(
            target=self._watch_loop,
            name=f"watchdog-{self._name}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Watchdog %s started (timeout=%.1fms)",
                    self._name, self._config.timeout_s * 1000)
    
    def stop(self) -> None:
        """Stop the watchdog thread."""
        self._stop_event.set()
        self._state = WatchdogState.STOPPED
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("Watchdog %s stopped", self._name)
    
    def begin_proc(self) -> None:
        """Mark the start of a Proc() execution.
        
        Call this at the beginning of Proc().
        """
        with self._lock:
            self._proc_start_time = time.monotonic()
            self._proc_in_progress = True
    
    def end_proc(self) -> None:
        """Mark the end of a Proc() execution.
        
        Call this at the end of Proc() (in finally block).
        """
        with self._lock:
            if not self._proc_in_progress:
                return
            
            elapsed = time.monotonic() - self._proc_start_time
            elapsed_ms = elapsed * 1000
            
            self._proc_in_progress = False
            self._stats["total_procs"] += 1
            
            # Update timing stats
            self._proc_times.append(elapsed_ms)
            if len(self._proc_times) > self._max_proc_times:
                self._proc_times.pop(0)
            
            self._stats["max_proc_time_ms"] = max(
                self._stats["max_proc_time_ms"],
                elapsed_ms,
            )
            self._stats["avg_proc_time_ms"] = (
                sum(self._proc_times) / len(self._proc_times)
            )
            
            # Check for recovery from timeout
            if self._state == WatchdogState.TIMEOUT:
                if elapsed < self._config.timeout_s:
                    self._consecutive_timeouts = 0
                    self._state = WatchdogState.WATCHING
                    logger.info("Watchdog %s: Proc() recovered", self._name)
                    if self._recovery_callback:
                        try:
                            self._recovery_callback(self._name)
                        except Exception as e:
                            logger.error("Recovery callback failed: %s", e)
    
    def _watch_loop(self) -> None:
        """Watchdog monitoring loop (runs in separate thread)."""
        while not self._stop_event.is_set():
            time.sleep(self._config.check_interval_s)
            
            with self._lock:
                if not self._proc_in_progress:
                    continue
                
                elapsed = time.monotonic() - self._proc_start_time
                
                if elapsed > self._config.timeout_s:
                    self._consecutive_timeouts += 1
                    self._stats["total_timeouts"] += 1
                    self._stats["last_timeout_time"] = time.time()
                    self._state = WatchdogState.TIMEOUT
                    
                    logger.error(
                        "Watchdog %s TIMEOUT: Proc() running for %.1fms "
                        "(threshold=%.1fms, consecutive=%d)",
                        self._name,
                        elapsed * 1000,
                        self._config.timeout_s * 1000,
                        self._consecutive_timeouts,
                    )
                    
                    # Invoke timeout callback (outside lock)
                    if self._timeout_callback:
                        try:
                            self._timeout_callback(self._name, elapsed * 1000)
                        except Exception as e:
                            logger.error("Timeout callback failed: %s", e)
                    
                    # Check for escalation
                    if self._consecutive_timeouts >= self._config.max_consecutive_timeouts:
                        logger.critical(
                            "Watchdog %s: %d consecutive timeouts — escalating!",
                            self._name, self._consecutive_timeouts,
                        )
    
    def register_timeout_callback(
        self,
        callback: Callable[[str, float], None],
    ) -> None:
        """Register callback for timeout events.
        
        Args:
            callback: Function(component_name, elapsed_ms)
        """
        self._timeout_callback = callback
    
    def register_recovery_callback(
        self,
        callback: Callable[[str], None],
    ) -> None:
        """Register callback for recovery events.
        
        Args:
            callback: Function(component_name)
        """
        self._recovery_callback = callback
    
    @property
    def state(self) -> WatchdogState:
        """Current watchdog state."""
        return self._state
    
    @property
    def is_timed_out(self) -> bool:
        """Check if currently in timeout state."""
        return self._state == WatchdogState.TIMEOUT
    
    def stats(self) -> Dict[str, Any]:
        """Return watchdog statistics."""
        with self._lock:
            return {
                **self._stats,
                "state": self._state.name,
                "consecutive_timeouts": self._consecutive_timeouts,
                "proc_in_progress": self._proc_in_progress,
            }


class WatchdogManager:
    """Manager for multiple component watchdogs."""
    
    _instance: Optional[WatchdogManager] = None
    _lock = threading.Lock()
    
    def __init__(self) -> None:
        self._watchdogs: Dict[str, ProcWatchdog] = {}
        self._global_timeout_callback: Optional[Callable[[str, float], None]] = None
    
    @classmethod
    def instance(cls) -> WatchdogManager:
        """Get singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    def create_watchdog(
        self,
        component_name: str,
        config: Optional[WatchdogConfig] = None,
    ) -> ProcWatchdog:
        """Create and register a watchdog for a component."""
        if component_name in self._watchdogs:
            logger.warning(
                "Watchdog for %s already exists, returning existing",
                component_name,
            )
            return self._watchdogs[component_name]
        
        watchdog = ProcWatchdog(component_name, config)
        
        # Register global callback if set
        if self._global_timeout_callback:
            watchdog.register_timeout_callback(self._global_timeout_callback)
        
        self._watchdogs[component_name] = watchdog
        return watchdog
    
    def get_watchdog(self, component_name: str) -> Optional[ProcWatchdog]:
        """Get watchdog by component name."""
        return self._watchdogs.get(component_name)
    
    def start_all(self) -> None:
        """Start all registered watchdogs."""
        for watchdog in self._watchdogs.values():
            watchdog.start()
    
    def stop_all(self) -> None:
        """Stop all registered watchdogs."""
        for watchdog in self._watchdogs.values():
            watchdog.stop()
    
    def set_global_timeout_callback(
        self,
        callback: Callable[[str, float], None],
    ) -> None:
        """Set callback for all watchdog timeouts."""
        self._global_timeout_callback = callback
        for watchdog in self._watchdogs.values():
            watchdog.register_timeout_callback(callback)
    
    def stats(self) -> Dict[str, Dict[str, Any]]:
        """Return stats for all watchdogs."""
        return {
            name: watchdog.stats()
            for name, watchdog in self._watchdogs.items()
        }
