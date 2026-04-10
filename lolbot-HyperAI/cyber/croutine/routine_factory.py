#!/usr/bin/env python3
"""
cyber/croutine/routine_factory.py — Coroutine Factory
======================================================

从 Apollo `cyber/croutine/routine_factory.h` 这个好例子开始。然后, 遵循该模式
实现一个新的 `RoutineFactory`, 让系统可以统一创建和管理协程实例。

Apollo reference:
    cyber/croutine/routine_factory.h   — CreateRoutine template

位置: lolbot-HyperAI/cyber/croutine/routine_factory.py
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Type

from cyber.croutine.croutine import CRoutine, FunctionRoutine, RoutineState


@dataclass
class RoutineConfig:
    """Configuration for routine creation."""
    name: str = ""
    priority: int = 0
    group_name: str = "default"
    processor_id: int = -1


class RoutineFactory:
    """
    Factory for creating and managing CRoutine instances.
    
    Apollo equivalent: cyber/croutine/routine_factory.h
    
    This factory provides a centralized way to create routines and
    track them for monitoring and debugging.
    
    Usage::
    
        factory = RoutineFactory.instance()
        
        # Create from function
        routine = factory.create_routine(
            my_callback,
            config=RoutineConfig(name="worker", priority=10)
        )
        
        # Create custom routine
        routine = factory.create_routine(
            MyCustomRoutine,
            config=RoutineConfig(name="custom")
        )
    """
    
    _instance: Optional[RoutineFactory] = None
    _instance_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> RoutineFactory:
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
            cls._instance = None
    
    def __init__(self) -> None:
        self._routines: Dict[str, CRoutine] = {}
        self._lock = threading.Lock()
        self._routine_counter = 0
        
        # Statistics
        self._stats = {
            "total_created": 0,
            "total_destroyed": 0,
        }
    
    def create_routine(
        self,
        target: Callable[[], bool] | Type[CRoutine],
        config: Optional[RoutineConfig] = None,
    ) -> CRoutine:
        """Create a new CRoutine.
        
        Apollo equivalent: CreateRoutine<T>()
        
        Args:
            target: Either a callable (wrapped in FunctionRoutine) or
                    a CRoutine subclass to instantiate
            config: Routine configuration
        
        Returns:
            The created routine
        """
        cfg = config or RoutineConfig()
        
        with self._lock:
            self._routine_counter += 1
            if not cfg.name:
                cfg.name = f"routine_{self._routine_counter}"
        
        # Create routine based on target type
        if isinstance(target, type) and issubclass(target, CRoutine):
            routine = target(
                name=cfg.name,
                priority=cfg.priority,
                group_name=cfg.group_name,
            )
        elif callable(target):
            routine = FunctionRoutine(
                func=target,
                name=cfg.name,
                priority=cfg.priority,
            )
        else:
            raise TypeError(f"Invalid target type: {type(target)}")
        
        # Register routine
        with self._lock:
            self._routines[routine.id] = routine
            self._stats["total_created"] += 1
        
        return routine
    
    def destroy_routine(self, routine_id: str) -> bool:
        """Destroy a routine.
        
        Args:
            routine_id: ID of routine to destroy
        
        Returns:
            True if routine was found and destroyed
        """
        with self._lock:
            routine = self._routines.pop(routine_id, None)
            if routine is None:
                return False
            
            routine.stop()
            self._stats["total_destroyed"] += 1
            return True
    
    def get_routine(self, routine_id: str) -> Optional[CRoutine]:
        """Get routine by ID."""
        with self._lock:
            return self._routines.get(routine_id)
    
    def get_routines_by_group(self, group_name: str) -> List[CRoutine]:
        """Get all routines in a group."""
        with self._lock:
            return [
                r for r in self._routines.values()
                if r.context.group_name == group_name
            ]
    
    def get_routines_by_state(self, state: RoutineState) -> List[CRoutine]:
        """Get all routines in a specific state."""
        with self._lock:
            return [r for r in self._routines.values() if r.state == state]
    
    @property
    def routine_count(self) -> int:
        """Number of active routines."""
        with self._lock:
            return len(self._routines)
    
    def stats(self) -> Dict:
        """Get factory statistics."""
        with self._lock:
            state_counts = {}
            for routine in self._routines.values():
                state = routine.state.name
                state_counts[state] = state_counts.get(state, 0) + 1
            
            return {
                **self._stats,
                "active_routines": len(self._routines),
                "state_distribution": state_counts,
            }
    
    def clear(self) -> int:
        """Stop and remove all routines.
        
        Returns:
            Number of routines cleared
        """
        with self._lock:
            count = len(self._routines)
            for routine in self._routines.values():
                routine.stop()
            self._routines.clear()
            return count
