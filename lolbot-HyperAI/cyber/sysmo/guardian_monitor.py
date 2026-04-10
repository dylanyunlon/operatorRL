"""
cyber/sysmo/guardian_monitor.py — Guardian Timeout Monitor
============================================================

Apollo reference:
    cyber/sysmo/sysmo.cc           SystemMonitor
    modules/guardian/guardian.cc   Guardian module

查看 Apollo sysmo + guardian 上现有的实现方式，理解其模式，
特别是 **系统级监控** 和 **紧急降级** 是如何分离的。

从 Apollo Guardian 模块这个好例子开始。然后，遵循该模式实现
一个新的 `GuardianMonitor`，让系统可以全局监控命令超时，
并能在多个组件同时超时时触发全局降级。

Design notes:
    - Monitors all component timeout states
    - Aggregates timeout signals from multiple sources
    - Triggers global estop when threshold exceeded
    - Recovery coordination across components

Claude30: Initial implementation
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set

from cyber.logger.cyber_logger import get_logger

logger = get_logger("cyber.sysmo.guardian")


class GuardianState(Enum):
    """Global guardian state."""
    NORMAL = auto()           # All systems nominal
    DEGRADED = auto()         # Some components timed out
    ESTOP = auto()            # Emergency stop
    RECOVERING = auto()       # Recovering from estop


@dataclass
class GuardianMonitorConfig:
    """Guardian monitor configuration."""
    # Minimum components that must timeout before global degradation
    min_timeout_components: int = 2
    
    # Time window for counting timeouts (seconds)
    timeout_window_s: float = 5.0
    
    # Time to wait before attempting recovery (seconds)
    recovery_wait_s: float = 10.0
    
    # Monitoring interval (seconds)
    monitor_interval_s: float = 1.0
    
    # Components to monitor
    monitored_components: List[str] = field(default_factory=lambda: [
        "canbus", "perception", "prediction", "planning", "control"
    ])


@dataclass
class ComponentTimeoutEvent:
    """Record of a component timeout event."""
    component: str
    timestamp: float
    duration_ms: float
    is_recovered: bool = False


class GuardianMonitor:
    """Global guardian monitor for system-wide timeout handling.
    
    This monitors timeout events from all components and makes
    global decisions about system degradation. When multiple
    components timeout simultaneously, it's often a sign of
    a systemic issue (e.g., data source stalled).
    
    Apollo pattern: Guardian module monitors control commands
    and triggers emergency stop when commands don't arrive.
    
    Usage::
    
        monitor = GuardianMonitor(config)
        monitor.register_estop_callback(on_estop)
        monitor.start()
        
        # Components report timeouts:
        monitor.report_timeout("canbus", 250.0)
        monitor.report_timeout("perception", 150.0)
        
        # Monitor checks and may trigger estop
        
        # Components report recovery:
        monitor.report_recovery("canbus")
    """
    
    _instance: Optional[GuardianMonitor] = None
    _instance_lock = threading.Lock()
    
    def __init__(self, config: Optional[GuardianMonitorConfig] = None) -> None:
        self._config = config or GuardianMonitorConfig()
        
        self._state = GuardianState.NORMAL
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        
        # Timeout tracking
        self._timeout_events: List[ComponentTimeoutEvent] = []
        self._currently_timed_out: Set[str] = set()
        self._last_estop_time: float = 0.0
        
        # Callbacks
        self._estop_callback: Optional[Callable[[], None]] = None
        self._recovery_callback: Optional[Callable[[], None]] = None
        self._degraded_callback: Optional[Callable[[Set[str]], None]] = None
        
        # Statistics
        self._stats = {
            "total_timeout_events": 0,
            "total_estops": 0,
            "total_recoveries": 0,
            "current_timed_out": 0,
            "last_estop_time": 0.0,
        }
    
    @classmethod
    def instance(cls) -> GuardianMonitor:
        """Get singleton instance."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    def start(self) -> None:
        """Start the guardian monitor thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("GuardianMonitor already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="guardian-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("GuardianMonitor started")
    
    def stop(self) -> None:
        """Stop the guardian monitor thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("GuardianMonitor stopped")
    
    def report_timeout(self, component: str, duration_ms: float) -> None:
        """Report a timeout event from a component.
        
        Args:
            component: Name of the component that timed out
            duration_ms: How long the timeout lasted (ms)
        """
        if component not in self._config.monitored_components:
            return
        
        with self._lock:
            event = ComponentTimeoutEvent(
                component=component,
                timestamp=time.time(),
                duration_ms=duration_ms,
            )
            self._timeout_events.append(event)
            self._currently_timed_out.add(component)
            self._stats["total_timeout_events"] += 1
            self._stats["current_timed_out"] = len(self._currently_timed_out)
            
            logger.warning(
                "Guardian: timeout reported from %s (%.1fms), "
                "total timed out: %d",
                component, duration_ms, len(self._currently_timed_out),
            )
    
    def report_recovery(self, component: str) -> None:
        """Report recovery from timeout.
        
        Args:
            component: Name of the component that recovered
        """
        with self._lock:
            self._currently_timed_out.discard(component)
            self._stats["current_timed_out"] = len(self._currently_timed_out)
            
            # Mark events as recovered
            for event in self._timeout_events:
                if event.component == component and not event.is_recovered:
                    event.is_recovered = True
            
            logger.info(
                "Guardian: recovery reported from %s, "
                "remaining timed out: %d",
                component, len(self._currently_timed_out),
            )
    
    def _monitor_loop(self) -> None:
        """Guardian monitoring loop (runs in separate thread)."""
        while not self._stop_event.is_set():
            time.sleep(self._config.monitor_interval_s)
            self._check_and_update_state()
    
    def _check_and_update_state(self) -> None:
        """Check timeout state and update guardian state."""
        current_time = time.time()
        
        with self._lock:
            # Clean up old timeout events
            cutoff = current_time - self._config.timeout_window_s
            self._timeout_events = [
                e for e in self._timeout_events
                if e.timestamp > cutoff
            ]
            
            # Count recent unrecovered timeouts
            recent_timeouts = sum(
                1 for e in self._timeout_events
                if not e.is_recovered
            )
            
            # State transitions
            old_state = self._state
            
            if self._state == GuardianState.NORMAL:
                if len(self._currently_timed_out) >= self._config.min_timeout_components:
                    self._state = GuardianState.DEGRADED
                    logger.warning(
                        "Guardian: entering DEGRADED state "
                        "(%d components timed out: %s)",
                        len(self._currently_timed_out),
                        ", ".join(self._currently_timed_out),
                    )
                    if self._degraded_callback:
                        try:
                            self._degraded_callback(set(self._currently_timed_out))
                        except Exception as e:
                            logger.error("Degraded callback failed: %s", e)
            
            elif self._state == GuardianState.DEGRADED:
                if len(self._currently_timed_out) == 0:
                    self._state = GuardianState.RECOVERING
                elif recent_timeouts >= len(self._config.monitored_components) - 1:
                    # Almost all components timed out — escalate to ESTOP
                    self._state = GuardianState.ESTOP
                    self._last_estop_time = current_time
                    self._stats["total_estops"] += 1
                    self._stats["last_estop_time"] = current_time
                    logger.critical(
                        "Guardian: entering ESTOP state! "
                        "(%d/%d components timed out)",
                        recent_timeouts,
                        len(self._config.monitored_components),
                    )
                    if self._estop_callback:
                        try:
                            self._estop_callback()
                        except Exception as e:
                            logger.error("Estop callback failed: %s", e)
            
            elif self._state == GuardianState.ESTOP:
                # Wait for recovery
                if len(self._currently_timed_out) == 0:
                    if current_time - self._last_estop_time > self._config.recovery_wait_s:
                        self._state = GuardianState.RECOVERING
            
            elif self._state == GuardianState.RECOVERING:
                if len(self._currently_timed_out) == 0:
                    self._state = GuardianState.NORMAL
                    self._stats["total_recoveries"] += 1
                    logger.info("Guardian: recovered to NORMAL state")
                    if self._recovery_callback:
                        try:
                            self._recovery_callback()
                        except Exception as e:
                            logger.error("Recovery callback failed: %s", e)
                elif len(self._currently_timed_out) >= self._config.min_timeout_components:
                    # Relapsed
                    self._state = GuardianState.DEGRADED
            
            if old_state != self._state:
                logger.info(
                    "Guardian state transition: %s -> %s",
                    old_state.name, self._state.name,
                )
    
    def register_estop_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for estop events."""
        self._estop_callback = callback
    
    def register_recovery_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for recovery events."""
        self._recovery_callback = callback
    
    def register_degraded_callback(
        self,
        callback: Callable[[Set[str]], None],
    ) -> None:
        """Register callback for degraded state (receives set of timed-out components)."""
        self._degraded_callback = callback
    
    @property
    def state(self) -> GuardianState:
        """Current guardian state."""
        return self._state
    
    @property
    def is_estop(self) -> bool:
        """Check if in estop state."""
        return self._state == GuardianState.ESTOP
    
    @property
    def is_degraded(self) -> bool:
        """Check if in degraded or worse state."""
        return self._state in (
            GuardianState.DEGRADED,
            GuardianState.ESTOP,
            GuardianState.RECOVERING,
        )
    
    def get_timed_out_components(self) -> Set[str]:
        """Get currently timed out components."""
        with self._lock:
            return set(self._currently_timed_out)
    
    def stats(self) -> Dict[str, Any]:
        """Return guardian statistics."""
        with self._lock:
            return {
                **self._stats,
                "state": self._state.name,
                "timed_out_components": list(self._currently_timed_out),
                "recent_timeout_count": len(self._timeout_events),
            }
