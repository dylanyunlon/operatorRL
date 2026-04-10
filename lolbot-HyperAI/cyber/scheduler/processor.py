#!/usr/bin/env python3
"""
cyber/scheduler/processor.py — Coroutine Processor
===================================================

从 Apollo `cyber/scheduler/processor.cc` 这个好例子开始。然后, 遵循该模式实现
一个新的 `Processor`, 让系统可以在独立线程中运行协程队列, 并能通过
ProcessorContext 管理处理器状态。

Apollo reference:
    cyber/scheduler/processor.cc   — Processor::Run/BindContext
    cyber/scheduler/processor.h    — Processor class

位置: lolbot-HyperAI/cyber/scheduler/processor.py
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from cyber.croutine.croutine import CRoutine
    from cyber.scheduler.processor_context import ProcessorContext


class ProcessorState(Enum):
    """Processor lifecycle state."""
    CREATED = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()


@dataclass
class ProcessorConfig:
    """Configuration for a processor."""
    id: int = 0
    name: str = ""
    bind_cpu: int = -1  # CPU affinity (-1 = no affinity)
    thread_priority: int = 0


class Processor:
    """
    A processor that runs CRoutines in its own thread.
    
    Apollo equivalent: cyber/scheduler/processor.cc
    
    Each Processor has:
    - A dedicated thread for running routines
    - A ProcessorContext for selecting which routine to run
    - Statistics for monitoring
    
    The Processor runs a loop that:
    1. Gets the next routine from the context
    2. Executes the routine
    3. Returns the routine to the context
    4. Repeats
    
    Usage::
    
        processor = Processor(ProcessorConfig(id=0, name="proc_0"))
        processor.bind_context(my_context)
        processor.start()
        
        # Add routines to context
        context.enqueue(routine)
        
        processor.stop()
    """
    
    def __init__(self, config: Optional[ProcessorConfig] = None) -> None:
        self._config = config or ProcessorConfig()
        self._id = self._config.id
        self._name = self._config.name or f"processor_{self._id}"
        
        self._context: Optional[ProcessorContext] = None
        self._state = ProcessorState.CREATED
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        
        # Statistics
        self._stats = {
            "total_iterations": 0,
            "total_routines_run": 0,
            "idle_iterations": 0,
            "last_routine_time_us": 0.0,
            "max_routine_time_us": 0.0,
        }
    
    # ─── Lifecycle ─────────────────────────────────────────────────────────
    
    def bind_context(self, context: ProcessorContext) -> None:
        """Bind a ProcessorContext to this processor.
        
        Apollo equivalent: Processor::BindContext()
        """
        with self._lock:
            self._context = context
            context.set_processor(self)
    
    def start(self) -> bool:
        """Start the processor thread.
        
        Apollo equivalent: Processor::Start()
        """
        with self._lock:
            if self._state == ProcessorState.RUNNING:
                return True
            
            if self._context is None:
                return False
            
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name=self._name,
                daemon=True,
            )
            self._thread.start()
            self._state = ProcessorState.RUNNING
            return True
    
    def stop(self) -> None:
        """Stop the processor thread.
        
        Apollo equivalent: Processor::Stop()
        """
        with self._lock:
            if self._state != ProcessorState.RUNNING:
                return
            self._state = ProcessorState.STOPPING
            self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        
        with self._lock:
            self._state = ProcessorState.STOPPED
            self._thread = None
    
    def _run_loop(self) -> None:
        """Main processing loop.
        
        Apollo equivalent: Processor::Run()
        """
        while not self._stop_event.is_set():
            self._stats["total_iterations"] += 1
            
            # Get next routine from context
            routine = self._context.next_routine() if self._context else None
            
            if routine is None:
                # No routine available, brief sleep
                self._stats["idle_iterations"] += 1
                time.sleep(0.001)  # 1ms idle wait
                continue
            
            # Execute routine
            start_us = time.monotonic() * 1e6
            
            try:
                should_continue = routine.execute()
            except Exception as e:
                should_continue = False
            
            elapsed_us = time.monotonic() * 1e6 - start_us
            self._stats["last_routine_time_us"] = elapsed_us
            self._stats["max_routine_time_us"] = max(
                self._stats["max_routine_time_us"],
                elapsed_us
            )
            self._stats["total_routines_run"] += 1
            
            # Return routine to context if it should continue
            if should_continue and self._context:
                self._context.return_routine(routine)
    
    # ─── Introspection ─────────────────────────────────────────────────────
    
    @property
    def id(self) -> int:
        return self._id
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def state(self) -> ProcessorState:
        with self._lock:
            return self._state
    
    @property
    def is_running(self) -> bool:
        return self.state == ProcessorState.RUNNING
    
    def stats(self) -> Dict:
        """Get processor statistics."""
        return {
            "id": self._id,
            "name": self._name,
            "state": self._state.name,
            **self._stats,
            "has_context": self._context is not None,
        }
    
    def __repr__(self) -> str:
        return f"Processor(id={self._id}, name={self._name}, state={self._state.name})"
