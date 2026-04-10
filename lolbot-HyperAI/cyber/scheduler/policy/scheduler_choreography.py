#!/usr/bin/env python3
"""
cyber/scheduler/policy/scheduler_choreography.py — Choreography Scheduler
==========================================================================

从 Apollo `cyber/scheduler/policy/scheduler_choreography.cc` 这个好例子开始。
然后, 遵循该模式实现一个新的 `SchedulerChoreography`, 让系统可以使用优先级
调度策略, 确保高优先级任务优先执行。

Apollo reference:
    cyber/scheduler/policy/scheduler_choreography.cc   — SchedulerChoreography

位置: lolbot-HyperAI/cyber/scheduler/policy/scheduler_choreography.py
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from cyber.croutine.croutine import CRoutine
from cyber.scheduler.processor import Processor, ProcessorConfig
from cyber.scheduler.processor_context import ChoreographyContext, ContextConfig


@dataclass
class ChoreographyConfig:
    """Configuration for choreography scheduler."""
    num_processors: int = 4
    processor_name_prefix: str = "choreo_proc"
    default_queue_size: int = 1000
    # Priority affinity: map priority ranges to specific processors
    priority_affinity: Dict[int, int] = field(default_factory=dict)


class SchedulerChoreography:
    """
    Priority-based choreography scheduler.
    
    Apollo equivalent: cyber/scheduler/policy/scheduler_choreography.cc
    
    The Choreography scheduler uses priority to determine execution order.
    High-priority routines are always executed before low-priority ones.
    
    Features:
    - Priority-based scheduling
    - Affinity binding (routines can prefer specific processors)
    - Better for heterogeneous workloads
    - Real-time friendly
    
    Usage::
    
        scheduler = SchedulerChoreography(
            ChoreographyConfig(num_processors=4)
        )
        scheduler.start()
        
        # Create high-priority routine
        routine = FunctionRoutine(critical_func, priority=100)
        scheduler.dispatch(routine)
        
        scheduler.stop()
    """
    
    _instance: Optional[SchedulerChoreography] = None
    _instance_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> SchedulerChoreography:
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
    
    def __init__(self, config: Optional[ChoreographyConfig] = None) -> None:
        self._config = config or ChoreographyConfig()
        
        self._processors: List[Processor] = []
        self._contexts: List[ChoreographyContext] = []
        self._routine_affinity: Dict[str, int] = {}  # routine_id -> processor_id
        self._lock = threading.Lock()
        self._running = False
        
        # Create processors and contexts
        self._create_processors()
        
        # Statistics
        self._stats = {
            "total_dispatched": 0,
            "dispatch_failures": 0,
            "affinity_hits": 0,
            "priority_dispatches": {
                "high": 0,    # priority >= 50
                "normal": 0,  # priority 10-49
                "low": 0,     # priority < 10
            },
        }
    
    def _create_processors(self) -> None:
        """Create processors and bind choreography contexts."""
        for i in range(self._config.num_processors):
            # Create choreography context (priority-aware)
            context = ChoreographyContext(ContextConfig(
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
        
        Apollo equivalent: SchedulerChoreography::Start()
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
        
        Apollo equivalent: SchedulerChoreography::Stop()
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
        
        Apollo equivalent: SchedulerChoreography::DispatchTask()
        
        Args:
            routine: The routine to dispatch
            processor_id: Target processor (-1 for auto-select)
        
        Returns:
            True if dispatch succeeded
        """
        with self._lock:
            if not self._running:
                return False
            
            # Determine target processor
            target_id = self._select_processor(routine, processor_id)
            target = self._contexts[target_id]
            
            success = target.enqueue(routine)
            if success:
                self._stats["total_dispatched"] += 1
                self._track_priority(routine.priority)
                
                # Remember affinity for this routine
                self._routine_affinity[routine.id] = target_id
            else:
                self._stats["dispatch_failures"] += 1
            
            return success
    
    def _select_processor(self, routine: CRoutine, hint: int) -> int:
        """Select the best processor for a routine.
        
        Selection order:
        1. Explicit hint (if valid)
        2. Previous affinity (if exists)
        3. Priority-based affinity (if configured)
        4. Least loaded processor
        """
        # Explicit hint
        if 0 <= hint < len(self._processors):
            return hint
        
        # Previous affinity
        if routine.id in self._routine_affinity:
            self._stats["affinity_hits"] += 1
            return self._routine_affinity[routine.id]
        
        # Priority affinity
        priority = routine.priority
        for p_range, proc_id in self._config.priority_affinity.items():
            if priority >= p_range and 0 <= proc_id < len(self._processors):
                return proc_id
        
        # Least loaded processor
        min_load = float('inf')
        min_id = 0
        for i, ctx in enumerate(self._contexts):
            load = ctx.size()
            if load < min_load:
                min_load = load
                min_id = i
        
        return min_id
    
    def _track_priority(self, priority: int) -> None:
        """Track priority distribution for statistics."""
        if priority >= 50:
            self._stats["priority_dispatches"]["high"] += 1
        elif priority >= 10:
            self._stats["priority_dispatches"]["normal"] += 1
        else:
            self._stats["priority_dispatches"]["low"] += 1
    
    def set_affinity(self, routine_id: str, processor_id: int) -> bool:
        """Set explicit affinity for a routine.
        
        Args:
            routine_id: The routine's ID
            processor_id: Target processor ID
        
        Returns:
            True if affinity was set
        """
        if processor_id < 0 or processor_id >= len(self._processors):
            return False
        
        with self._lock:
            self._routine_affinity[routine_id] = processor_id
        return True
    
    def clear_affinity(self, routine_id: str) -> None:
        """Clear affinity for a routine."""
        with self._lock:
            self._routine_affinity.pop(routine_id, None)
    
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
            "type": "choreography",
            "running": self._running,
            "processor_count": len(self._processors),
            "total_queue_size": self.total_queue_size(),
            "affinity_entries": len(self._routine_affinity),
            **self._stats,
            "processors": processor_stats,
            "contexts": context_stats,
        }
