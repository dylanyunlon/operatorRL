#!/usr/bin/env python3
"""
cyber/croutine/croutine.py — Apollo-style Coroutine Implementation
===================================================================

从 Apollo `cyber/croutine/croutine.cc` 这个好例子开始。然后, 遵循该模式实现
一个新的 `CRoutine`, 让系统可以创建轻量级协程并在调度器中运行, 并能通过
状态机管理协程生命周期。接着 `yield()` 引入协作式调度, 使协程能够主动让出
CPU 时间片, 同时优化上下文切换开销。

Apollo reference:
    cyber/croutine/croutine.cc   — CRoutine::Run/Yield/Stop
    cyber/croutine/croutine.h    — RoutineState enum

位置: lolbot-HyperAI/cyber/croutine/croutine.py
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, Optional, Any
import uuid


class RoutineState(Enum):
    """Coroutine state machine.
    
    Apollo equivalent: cyber/croutine/croutine.h RoutineState
    """
    READY = auto()      # Ready to run
    RUNNING = auto()    # Currently executing
    SLEEPING = auto()   # Waiting for wake-up
    IO_WAIT = auto()    # Waiting for I/O
    DATA_WAIT = auto()  # Waiting for data
    FINISHED = auto()   # Completed execution


@dataclass
class RoutineContext:
    """Context data for a coroutine.
    
    Apollo equivalent: cyber/croutine/croutine.h context fields
    """
    routine_id: str = ""
    name: str = ""
    processor_id: int = -1
    priority: int = 0
    group_name: str = "default"
    created_at: float = field(default_factory=time.monotonic)
    
    # Statistics
    run_count: int = 0
    yield_count: int = 0
    total_run_time_us: float = 0.0
    last_run_time_us: float = 0.0


class CRoutine(ABC):
    """
    Apollo-style coroutine base class.
    
    Apollo equivalent: cyber/croutine/croutine.cc
    
    A CRoutine is a lightweight execution unit that can be scheduled
    by the Processor. Unlike threads, coroutines use cooperative
    multitasking - they must explicitly yield control.
    
    Usage::
    
        class MyRoutine(CRoutine):
            def run(self) -> bool:
                # Do work
                self.yield_()  # Yield control
                # More work
                return True  # Return False to stop
        
        routine = MyRoutine(name="my_routine")
        routine.start()
    """
    
    def __init__(
        self,
        name: str = "",
        priority: int = 0,
        group_name: str = "default",
    ) -> None:
        self._id = str(uuid.uuid4())[:8]
        self._context = RoutineContext(
            routine_id=self._id,
            name=name or f"routine_{self._id}",
            priority=priority,
            group_name=group_name,
        )
        self._state = RoutineState.READY
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._stop_requested = False
        
        # For tracking
        self._run_start_time: float = 0.0
    
    # ─── Abstract Interface ─────────────────────────────────────────────────
    
    @abstractmethod
    def run(self) -> bool:
        """Main execution logic.
        
        Apollo equivalent: CRoutine::Execute()
        
        Returns:
            True to continue running, False to stop
        """
        pass
    
    # ─── State Management (Apollo: SetState/GetState) ───────────────────────
    
    @property
    def state(self) -> RoutineState:
        """Get current state."""
        with self._lock:
            return self._state
    
    def set_state(self, state: RoutineState) -> None:
        """Set coroutine state."""
        with self._lock:
            self._state = state
    
    def is_ready(self) -> bool:
        return self.state == RoutineState.READY
    
    def is_running(self) -> bool:
        return self.state == RoutineState.RUNNING
    
    def is_sleeping(self) -> bool:
        return self.state == RoutineState.SLEEPING
    
    def is_finished(self) -> bool:
        return self.state == RoutineState.FINISHED
    
    # ─── Execution Control (Apollo: Run/Yield/Wake/Stop) ────────────────────
    
    def execute(self) -> bool:
        """Execute one iteration of the routine.
        
        Apollo equivalent: CRoutine::Run()
        
        Called by the Processor when this routine is scheduled.
        
        Returns:
            True if routine should continue, False if finished
        """
        if self._stop_requested:
            self.set_state(RoutineState.FINISHED)
            return False
        
        self.set_state(RoutineState.RUNNING)
        self._run_start_time = time.monotonic()
        self._context.run_count += 1
        
        try:
            should_continue = self.run()
        except Exception as e:
            # Log error but don't crash
            should_continue = False
        finally:
            run_time_us = (time.monotonic() - self._run_start_time) * 1e6
            self._context.last_run_time_us = run_time_us
            self._context.total_run_time_us += run_time_us
        
        if not should_continue:
            self.set_state(RoutineState.FINISHED)
            return False
        
        # Return to ready state unless sleeping
        if self.state == RoutineState.RUNNING:
            self.set_state(RoutineState.READY)
        
        return True
    
    def yield_(self) -> None:
        """Yield control back to the scheduler.
        
        Apollo equivalent: CRoutine::Yield()
        
        This is a cooperative yield - the routine voluntarily gives up
        CPU time so other routines can run.
        """
        self._context.yield_count += 1
        # In a real implementation, this would context-switch
        # For Python, we just track the yield
    
    def sleep(self, duration_ms: float = 0) -> None:
        """Put routine to sleep.
        
        Apollo equivalent: CRoutine::Sleep()
        """
        self.set_state(RoutineState.SLEEPING)
        self._wake_event.clear()
        if duration_ms > 0:
            self._wake_event.wait(timeout=duration_ms / 1000.0)
        else:
            self._wake_event.wait()
        if not self._stop_requested:
            self.set_state(RoutineState.READY)
    
    def wake(self) -> None:
        """Wake up a sleeping routine.
        
        Apollo equivalent: CRoutine::Wake()
        """
        self._wake_event.set()
    
    def stop(self) -> None:
        """Request routine to stop.
        
        Apollo equivalent: CRoutine::Stop()
        """
        self._stop_requested = True
        self._wake_event.set()
    
    # ─── Introspection ──────────────────────────────────────────────────────
    
    @property
    def id(self) -> str:
        return self._id
    
    @property
    def name(self) -> str:
        return self._context.name
    
    @property
    def priority(self) -> int:
        return self._context.priority
    
    @property
    def context(self) -> RoutineContext:
        return self._context
    
    def stats(self) -> Dict[str, Any]:
        """Get routine statistics."""
        return {
            "id": self._id,
            "name": self._context.name,
            "state": self._state.name,
            "priority": self._context.priority,
            "group": self._context.group_name,
            "run_count": self._context.run_count,
            "yield_count": self._context.yield_count,
            "total_run_time_us": self._context.total_run_time_us,
            "last_run_time_us": self._context.last_run_time_us,
            "age_s": time.monotonic() - self._context.created_at,
        }
    
    def __repr__(self) -> str:
        return f"CRoutine(id={self._id}, name={self.name}, state={self.state.name})"


class FunctionRoutine(CRoutine):
    """A CRoutine that wraps a simple function.
    
    Convenience class for creating routines from functions.
    
    Usage::
    
        def my_func():
            print("Hello")
            return True  # Continue
        
        routine = FunctionRoutine(my_func, name="greeter")
    """
    
    def __init__(
        self,
        func: Callable[[], bool],
        name: str = "",
        priority: int = 0,
    ) -> None:
        super().__init__(name=name, priority=priority)
        self._func = func
    
    def run(self) -> bool:
        return self._func()
