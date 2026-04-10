#!/usr/bin/env python3
"""
cyber/scheduler/processor_context.py — Processor Context
=========================================================

从 Apollo `cyber/scheduler/processor_context.cc` 这个好例子开始。然后, 遵循
该模式实现一个新的 `ProcessorContext`, 让每个处理器可以有独立的协程队列和
调度策略。

Apollo reference:
    cyber/scheduler/processor_context.cc   — ProcessorContext class
    cyber/scheduler/processor_context.h

位置: lolbot-HyperAI/cyber/scheduler/processor_context.py
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from cyber.croutine.croutine import CRoutine
    from cyber.scheduler.processor import Processor


@dataclass
class ContextConfig:
    """Configuration for processor context."""
    id: int = 0
    name: str = ""
    max_queue_size: int = 1000


class ProcessorContext(ABC):
    """
    Abstract base class for processor contexts.
    
    Apollo equivalent: cyber/scheduler/processor_context.cc
    
    The ProcessorContext manages the queue of routines for a Processor.
    Different scheduling policies are implemented by subclasses.
    
    Subclasses must implement:
    - next_routine(): Get the next routine to run
    - return_routine(): Return a routine after execution
    - enqueue(): Add a new routine to the queue
    """
    
    def __init__(self, config: Optional[ContextConfig] = None) -> None:
        self._config = config or ContextConfig()
        self._id = self._config.id
        self._name = self._config.name or f"context_{self._id}"
        self._processor: Optional[Processor] = None
        self._lock = threading.Lock()
        
        # Statistics
        self._stats = {
            "total_enqueued": 0,
            "total_dequeued": 0,
            "total_returned": 0,
        }
    
    @abstractmethod
    def next_routine(self) -> Optional[CRoutine]:
        """Get the next routine to run.
        
        Returns:
            The next routine, or None if queue is empty
        """
        pass
    
    @abstractmethod
    def return_routine(self, routine: CRoutine) -> None:
        """Return a routine after execution.
        
        Called when a routine yields or completes an iteration.
        """
        pass
    
    @abstractmethod
    def enqueue(self, routine: CRoutine) -> bool:
        """Add a routine to the queue.
        
        Returns:
            True if routine was added successfully
        """
        pass
    
    def set_processor(self, processor: Processor) -> None:
        """Bind this context to a processor."""
        self._processor = processor
    
    @property
    def id(self) -> int:
        return self._id
    
    @property
    def name(self) -> str:
        return self._name
    
    @abstractmethod
    def size(self) -> int:
        """Number of routines in the queue."""
        pass
    
    def stats(self) -> Dict:
        """Get context statistics."""
        return {
            "id": self._id,
            "name": self._name,
            "size": self.size(),
            **self._stats,
        }


class ClassicContext(ProcessorContext):
    """
    Classic FIFO context for processor.
    
    Apollo equivalent: Classic scheduler's context
    
    Uses a simple FIFO queue for routines.
    """
    
    def __init__(self, config: Optional[ContextConfig] = None) -> None:
        super().__init__(config)
        self._queue: Deque[CRoutine] = deque(maxlen=self._config.max_queue_size)
    
    def next_routine(self) -> Optional[CRoutine]:
        with self._lock:
            if not self._queue:
                return None
            routine = self._queue.popleft()
            self._stats["total_dequeued"] += 1
            return routine
    
    def return_routine(self, routine: CRoutine) -> None:
        with self._lock:
            if routine.is_ready():
                self._queue.append(routine)
                self._stats["total_returned"] += 1
    
    def enqueue(self, routine: CRoutine) -> bool:
        with self._lock:
            if len(self._queue) >= self._config.max_queue_size:
                return False
            self._queue.append(routine)
            self._stats["total_enqueued"] += 1
            return True
    
    def size(self) -> int:
        with self._lock:
            return len(self._queue)


class ChoreographyContext(ProcessorContext):
    """
    Priority-based context for processor.
    
    Apollo equivalent: Choreography scheduler's context
    
    Uses priority queues to ensure high-priority routines run first.
    """
    
    def __init__(self, config: Optional[ContextConfig] = None) -> None:
        super().__init__(config)
        # Priority buckets (higher index = higher priority)
        self._priority_queues: Dict[int, Deque[CRoutine]] = {}
        self._max_priority: int = 0
    
    def next_routine(self) -> Optional[CRoutine]:
        with self._lock:
            # Check queues from highest to lowest priority
            for priority in range(self._max_priority, -1, -1):
                queue = self._priority_queues.get(priority)
                if queue and len(queue) > 0:
                    routine = queue.popleft()
                    self._stats["total_dequeued"] += 1
                    return routine
            return None
    
    def return_routine(self, routine: CRoutine) -> None:
        if routine.is_ready():
            self.enqueue(routine)
            self._stats["total_returned"] += 1
    
    def enqueue(self, routine: CRoutine) -> bool:
        with self._lock:
            priority = routine.priority
            if priority not in self._priority_queues:
                self._priority_queues[priority] = deque()
            
            queue = self._priority_queues[priority]
            if len(queue) >= self._config.max_queue_size:
                return False
            
            queue.append(routine)
            self._max_priority = max(self._max_priority, priority)
            self._stats["total_enqueued"] += 1
            return True
    
    def size(self) -> int:
        with self._lock:
            return sum(len(q) for q in self._priority_queues.values())
