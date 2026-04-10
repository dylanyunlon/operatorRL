"""
modules/control/estop_handler.py — Emergency Stop Handler
===========================================================

Apollo reference:
    modules/control/control_component.cc   estop handling
    modules/guardian/guardian.cc           emergency brake

查看 Apollo control_component 上现有 estop 的实现方式，理解其模式，
特别是 **紧急停止触发** 和 **恢复逻辑** 是如何分离的。

从 Apollo control estop 这个好例子开始。然后，遵循该模式实现
一个新的 `EstopHandler`，让 lolbot-HyperAI 可以安全地处理紧急情况。

Design notes:
    - Singleton pattern for global estop state
    - Multiple trigger sources (timeout, manual, system)
    - Configurable recovery conditions
    - Callback hooks for estop events

Claude30: Initial implementation
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set

from cyber.logger.cyber_logger import get_logger

logger = get_logger("control.estop")


class EstopTrigger(Enum):
    """Estop trigger source."""
    NONE = auto()
    TIMEOUT = auto()           # Command timeout
    MANUAL = auto()            # User requested
    SYSTEM = auto()            # System error
    COMMUNICATION = auto()     # Communication fault
    GUARDIAN = auto()          # Guardian module
    EXTERNAL = auto()          # External signal


class EstopState(Enum):
    """Estop state machine."""
    INACTIVE = auto()          # Normal operation
    SOFT_ESTOP = auto()        # Soft stop (can auto-recover)
    HARD_ESTOP = auto()        # Hard stop (requires manual recovery)
    RECOVERING = auto()        # Recovery in progress


@dataclass
class EstopConfig:
    """Estop handler configuration."""
    # Allow auto-recovery from soft estop
    allow_auto_recovery: bool = True
    
    # Minimum time in estop before recovery (seconds)
    min_estop_duration_s: float = 2.0
    
    # Maximum estop duration before escalation (seconds)
    max_soft_estop_s: float = 30.0
    
    # Triggers that cause hard estop (no auto-recovery)
    hard_estop_triggers: Set[EstopTrigger] = field(default_factory=lambda: {
        EstopTrigger.MANUAL,
        EstopTrigger.SYSTEM,
    })


@dataclass
class EstopEvent:
    """Record of an estop event."""
    timestamp: float
    trigger: EstopTrigger
    reason: str
    state: EstopState
    recovered: bool = False
    recovery_time: float = 0.0


class EstopHandler:
    """Emergency stop handler for the control module.
    
    This manages the estop state machine and coordinates
    emergency stop across all components.
    
    Apollo pattern: control_component.cc handles estop from
    guardian module and applies emergency brake.
    
    For LoL:
    - Soft estop: stop issuing aggressive recommendations
    - Hard estop: stop all recommendations, show warning
    
    Usage::
    
        handler = EstopHandler.instance()
        handler.register_callback(on_estop)
        
        # Trigger estop
        handler.trigger(EstopTrigger.TIMEOUT, "command timeout")
        
        # Check state
        if handler.is_estopped:
            # Apply safe actions
            
        # Recover (if allowed)
        handler.request_recovery()
    """
    
    _instance: Optional[EstopHandler] = None
    _lock = threading.Lock()
    
    def __init__(self, config: Optional[EstopConfig] = None) -> None:
        self._config = config or EstopConfig()
        self._state = EstopState.INACTIVE
        self._state_lock = threading.Lock()
        
        # Current estop info
        self._current_trigger = EstopTrigger.NONE
        self._current_reason = ""
        self._estop_start_time = 0.0
        
        # Event history
        self._events: List[EstopEvent] = []
        self._max_events = 100
        
        # Callbacks
        self._estop_callbacks: List[Callable[[EstopTrigger, str], None]] = []
        self._recovery_callbacks: List[Callable[[], None]] = []
        
        # Statistics
        self._stats = {
            "total_estops": 0,
            "total_recoveries": 0,
            "soft_estops": 0,
            "hard_estops": 0,
            "current_estop_duration_s": 0.0,
        }
    
    @classmethod
    def instance(cls) -> EstopHandler:
        """Get singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None
    
    def trigger(
        self,
        trigger: EstopTrigger,
        reason: str = "",
    ) -> bool:
        """Trigger emergency stop.
        
        Args:
            trigger: Source of the estop
            reason: Human-readable reason
            
        Returns:
            True if estop was triggered (state changed)
        """
        with self._state_lock:
            if self._state in (EstopState.SOFT_ESTOP, EstopState.HARD_ESTOP):
                # Already in estop — check if we need to escalate
                if (
                    self._state == EstopState.SOFT_ESTOP
                    and trigger in self._config.hard_estop_triggers
                ):
                    self._state = EstopState.HARD_ESTOP
                    logger.warning(
                        "Estop escalated to HARD: trigger=%s reason=%s",
                        trigger.name, reason,
                    )
                return False
            
            # Determine estop type
            if trigger in self._config.hard_estop_triggers:
                self._state = EstopState.HARD_ESTOP
                self._stats["hard_estops"] += 1
            else:
                self._state = EstopState.SOFT_ESTOP
                self._stats["soft_estops"] += 1
            
            self._current_trigger = trigger
            self._current_reason = reason
            self._estop_start_time = time.time()
            self._stats["total_estops"] += 1
            
            # Record event
            event = EstopEvent(
                timestamp=self._estop_start_time,
                trigger=trigger,
                reason=reason,
                state=self._state,
            )
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events.pop(0)
            
            logger.error(
                "ESTOP triggered: state=%s trigger=%s reason=%s",
                self._state.name, trigger.name, reason,
            )
        
        # Invoke callbacks outside lock
        for callback in self._estop_callbacks:
            try:
                callback(trigger, reason)
            except Exception as e:
                logger.error("Estop callback failed: %s", e)
        
        return True
    
    def request_recovery(self, force: bool = False) -> bool:
        """Request recovery from estop.
        
        Args:
            force: Force recovery even from hard estop
            
        Returns:
            True if recovery was initiated
        """
        with self._state_lock:
            if self._state == EstopState.INACTIVE:
                return True  # Already normal
            
            if self._state == EstopState.HARD_ESTOP and not force:
                logger.warning(
                    "Cannot auto-recover from HARD_ESTOP (use force=True)"
                )
                return False
            
            # Check minimum duration
            duration = time.time() - self._estop_start_time
            if duration < self._config.min_estop_duration_s:
                logger.debug(
                    "Recovery too soon: %.1fs < %.1fs",
                    duration, self._config.min_estop_duration_s,
                )
                return False
            
            self._state = EstopState.RECOVERING
            logger.info("Estop recovery initiated")
        
        return True
    
    def complete_recovery(self) -> bool:
        """Complete recovery and return to normal state.
        
        Call this after recovery conditions are met.
        
        Returns:
            True if recovery completed
        """
        with self._state_lock:
            if self._state != EstopState.RECOVERING:
                return False
            
            # Mark event as recovered
            for event in reversed(self._events):
                if not event.recovered:
                    event.recovered = True
                    event.recovery_time = time.time()
                    break
            
            self._state = EstopState.INACTIVE
            self._current_trigger = EstopTrigger.NONE
            self._current_reason = ""
            self._stats["total_recoveries"] += 1
            
            logger.info("Estop recovery complete")
        
        # Invoke callbacks outside lock
        for callback in self._recovery_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Recovery callback failed: %s", e)
        
        return True
    
    def check_auto_recovery(self) -> bool:
        """Check if auto-recovery should happen.
        
        Call this periodically to handle auto-recovery from soft estop.
        
        Returns:
            True if recovery was completed
        """
        if not self._config.allow_auto_recovery:
            return False
        
        with self._state_lock:
            if self._state != EstopState.SOFT_ESTOP:
                return False
            
            duration = time.time() - self._estop_start_time
            self._stats["current_estop_duration_s"] = duration
            
            # Check if we've been in soft estop too long
            if duration > self._config.max_soft_estop_s:
                logger.warning(
                    "Soft estop exceeded max duration (%.1fs), escalating",
                    duration,
                )
                self._state = EstopState.HARD_ESTOP
                return False
        
        # Request and complete recovery
        if self.request_recovery():
            return self.complete_recovery()
        
        return False
    
    def register_estop_callback(
        self,
        callback: Callable[[EstopTrigger, str], None],
    ) -> None:
        """Register callback for estop events."""
        self._estop_callbacks.append(callback)
    
    def register_recovery_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for recovery events."""
        self._recovery_callbacks.append(callback)
    
    @property
    def state(self) -> EstopState:
        """Current estop state."""
        return self._state
    
    @property
    def is_estopped(self) -> bool:
        """Check if currently in any estop state."""
        return self._state in (
            EstopState.SOFT_ESTOP,
            EstopState.HARD_ESTOP,
        )
    
    @property
    def is_hard_estopped(self) -> bool:
        """Check if in hard estop (no auto-recovery)."""
        return self._state == EstopState.HARD_ESTOP
    
    @property
    def is_recovering(self) -> bool:
        """Check if recovery is in progress."""
        return self._state == EstopState.RECOVERING
    
    @property
    def current_trigger(self) -> EstopTrigger:
        """Get current estop trigger."""
        return self._current_trigger
    
    @property
    def current_reason(self) -> str:
        """Get current estop reason."""
        return self._current_reason
    
    def get_estop_duration_s(self) -> float:
        """Get current estop duration in seconds."""
        if self._estop_start_time <= 0:
            return 0.0
        return time.time() - self._estop_start_time
    
    def get_recent_events(self, count: int = 10) -> List[EstopEvent]:
        """Get recent estop events."""
        return self._events[-count:]
    
    def stats(self) -> Dict[str, Any]:
        """Return estop statistics."""
        with self._state_lock:
            if self._estop_start_time > 0 and self._state != EstopState.INACTIVE:
                self._stats["current_estop_duration_s"] = (
                    time.time() - self._estop_start_time
                )
            return {
                **self._stats,
                "state": self._state.name,
                "trigger": self._current_trigger.name,
                "reason": self._current_reason,
                "event_count": len(self._events),
            }
