#!/usr/bin/env python3
"""
cyber/scheduler/policy/scheduler_classic.py — Classic Scheduler Policy
========================================================================

从 Apollo `cyber/scheduler/policy/scheduler_classic.cc` 这个好例子开始。
然后, 遵循该模式实现一个新的 `SchedulerClassic`, 让系统可以使用轮询调度策略
分配协程到处理器。

Apollo reference:
    cyber/scheduler/policy/scheduler_classic.cc   — SchedulerClassic class

位置: lolbot-HyperAI/cyber/scheduler/policy/scheduler_classic.py
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from cyber.croutine.croutine import CRoutine
from cyber.scheduler.processor import Processor, ProcessorConfig
from cyber.scheduler.processor_context import ClassicContext, ContextConfig


@dataclass
class ClassicSchedulerConfig:
    """Configuration for classic scheduler."""
    num_processors: int = 4
    processor_name_prefix: str = "classic_proc"
    default_queue_size: int = 1000


class SchedulerClassic:
    """
    Classic round-robin scheduler.
    
    Apollo equivalent: cyber/scheduler/policy/scheduler_classic.cc
    
    The Classic scheduler uses a simple round-robin strategy to distribute
    routines across processors. Each processor has its own FIFO queue.
    
    Features:
    - Equal distribution of work
    - Simple and predictable behavior
    - Good for homogeneous workloads
    
    Usage::
    
        scheduler = SchedulerClassic(
            ClassicSchedulerConfig(num_processors=4)
        )
        scheduler.start()
        
        # Create and dispatch routine
        routine = FunctionRoutine(my_func)
        scheduler.dispatch(routine)
        
        scheduler.stop()
    """
    
    _instance: Optional[SchedulerClassic] = None
    _instance_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> SchedulerClassic:
        """Get singleton instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None
    
    def __init__(self, config: Optional[ClassicSchedulerConfig] = None) -> None:
        self._config = config or ClassicSchedulerConfig()
        
        self._processors: List[Processor] = []
        self._contexts: List[ClassicContext] = []
        self._dispatch_index: int = 0
        self._lock = threading.Lock()
        self._running = False
        
        # Create processors and contexts
        self._create_processors()
        
        # Statistics
        self._stats = {
            "total_dispatched": 0,
            "dispatch_failures": 0,
        }
    
    def _create_processors(self) -> None:
        """Create processors and bind contexts."""
        for i in range(self._config.num_processors):
            # Create context
            context = ClassicContext(ContextConfig(
                id=i,
                name=f"{self._config.processor_name_prefix}_ctx_{i}",
                max_queue_size=self._config.default_queue_size,
            ))
            self._contexts.append(context)
            
            # Create processor
            processor = Processor(ProcessorConfig(
                id=i,
                name=f"{self._config.processor_name_prefix}_{i}",
            ))
            processor.bind_context(context)
            self._processors.append(processor)
    
    # ─── Lifecycle ─────────────────────────────────────────────────────────
    
    def start(self) -> bool:
        """Start all processors.
        
        Apollo equivalent: SchedulerClassic::Init() + Start()
        """
        with self._lock:
            if self._running:
                return True
            
            for processor in self._processors:
                processor.start()
            
            self._running = True
            return True
    
    def stop(self) -> None:
        """Stop all processors.
        
        Apollo equivalent: SchedulerClassic::Stop()
        """
        with self._lock:
            if not self._running:
                return
            
            for processor in self._processors:
                processor.stop()
            
            self._running = False
    
    # ─── Dispatch ─────────────────────────────────────────────────────────
    
    def dispatch(self, routine: CRoutine, processor_id: int = -1) -> bool:
        """Dispatch a routine to a processor.
        
        Apollo equivalent: SchedulerClassic::DispatchTask()
        
        Args:
            routine: The routine to dispatch
            processor_id: Target processor (-1 for round-robin)
        
        Returns:
            True if dispatch succeeded
        """
        with self._lock:
            if not self._running:
                return False
            
            if processor_id >= 0 and processor_id < len(self._contexts):
                # Specific processor
                target = self._contexts[processor_id]
            else:
                # Round-robin
                target = self._contexts[self._dispatch_index]
                self._dispatch_index = (
                    (self._dispatch_index + 1) % len(self._contexts)
                )
            
            success = target.enqueue(routine)
            if success:
                self._stats["total_dispatched"] += 1
            else:
                self._stats["dispatch_failures"] += 1
            
            return success
    
    def dispatch_by_name(self, routine: CRoutine, group_name: str) -> bool:
        """Dispatch to processor by group name.
        
        Maps group names to processor IDs for affinity.
        """
        # Simple hash-based mapping
        processor_id = hash(group_name) % len(self._processors)
        return self.dispatch(routine, processor_id)
    
    # ─── Introspection ─────────────────────────────────────────────────────
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def processor_count(self) -> int:
        return len(self._processors)
    
    def total_queue_size(self) -> int:
        """Total routines queued across all contexts."""
        return sum(ctx.size() for ctx in self._contexts)
    
    def stats(self) -> Dict:
        """Get scheduler statistics."""
        processor_stats = [p.stats() for p in self._processors]
        context_stats = [c.stats() for c in self._contexts]
        
        return {
            "type": "classic",
            "running": self._running,
            "processor_count": len(self._processors),
            "total_queue_size": self.total_queue_size(),
            **self._stats,
            "processors": processor_stats,
            "contexts": context_stats,
        }
