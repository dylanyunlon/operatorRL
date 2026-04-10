#!/usr/bin/env python3
"""
cyber/mainboard/module_controller.py — Module Controller
==========================================================

从 Apollo `cyber/mainboard/module_controller.cc` 这个好例子开始。然后, 遵循
该模式实现一个新的 `ModuleController`, 让系统可以统一管理和控制所有模块。

Apollo reference:
    cyber/mainboard/module_controller.cc   — ModuleController class
    cyber/mainboard/module_controller.h

位置: lolbot-HyperAI/cyber/mainboard/module_controller.py
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type


class ModuleState(Enum):
    """Module lifecycle state."""
    UNLOADED = auto()
    LOADED = auto()
    INITIALIZED = auto()
    RUNNING = auto()
    STOPPED = auto()
    ERROR = auto()


@dataclass
class ModuleInfo:
    """Information about a loaded module."""
    name: str
    class_name: str
    state: ModuleState = ModuleState.UNLOADED
    instance: Optional[Any] = None
    load_time: float = 0.0
    init_time: float = 0.0
    error_message: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "class_name": self.class_name,
            "state": self.state.name,
            "load_time": self.load_time,
            "init_time": self.init_time,
            "error_message": self.error_message,
        }


@dataclass
class ModuleControllerConfig:
    """Configuration for module controller."""
    auto_start: bool = True
    shutdown_timeout_s: float = 5.0
    init_timeout_s: float = 30.0


class ModuleController:
    """
    Controller for loading and managing modules.
    
    Apollo equivalent: cyber/mainboard/module_controller.cc
    
    The ModuleController is responsible for:
    - Loading module classes from configuration
    - Initializing modules in correct order
    - Starting and stopping modules
    - Managing module lifecycle
    
    Usage::
    
        controller = ModuleController.instance()
        
        # Load a module
        controller.load_module("perception", PerceptionComponent)
        
        # Initialize all modules
        controller.init_all()
        
        # Start all modules
        controller.start_all()
        
        # Stop and cleanup
        controller.shutdown()
    """
    
    _instance: Optional[ModuleController] = None
    _instance_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> ModuleController:
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
                cls._instance.shutdown()
            cls._instance = None
    
    def __init__(self, config: Optional[ModuleControllerConfig] = None) -> None:
        self._config = config or ModuleControllerConfig()
        self._modules: Dict[str, ModuleInfo] = {}
        self._load_order: List[str] = []
        self._lock = threading.RLock()
        
        # Callbacks
        self._on_module_state_change: List[Callable[[str, ModuleState, ModuleState], None]] = []
        
        # Statistics
        self._stats = {
            "total_loaded": 0,
            "total_initialized": 0,
            "total_started": 0,
            "total_errors": 0,
        }
    
    # ─── Module Loading ────────────────────────────────────────────────────
    
    def load_module(
        self,
        name: str,
        module_class: Type[Any],
        *args,
        **kwargs,
    ) -> bool:
        """Load a module.
        
        Apollo equivalent: ModuleController::LoadModule()
        
        Args:
            name: Unique module name
            module_class: Module class to instantiate
            *args, **kwargs: Arguments for module constructor
        
        Returns:
            True if module was loaded successfully
        """
        with self._lock:
            if name in self._modules:
                return False
            
            info = ModuleInfo(
                name=name,
                class_name=module_class.__name__,
            )
            
            try:
                start_time = time.monotonic()
                info.instance = module_class(*args, **kwargs)
                info.load_time = time.monotonic() - start_time
                info.state = ModuleState.LOADED
                
                self._modules[name] = info
                self._load_order.append(name)
                self._stats["total_loaded"] += 1
                
                return True
                
            except Exception as e:
                info.state = ModuleState.ERROR
                info.error_message = str(e)
                self._modules[name] = info
                self._stats["total_errors"] += 1
                return False
    
    def unload_module(self, name: str) -> bool:
        """Unload a module.
        
        Args:
            name: Module name
        
        Returns:
            True if module was unloaded
        """
        with self._lock:
            if name not in self._modules:
                return False
            
            info = self._modules[name]
            
            # Stop if running
            if info.state == ModuleState.RUNNING:
                self.stop_module(name)
            
            # Cleanup
            if info.instance and hasattr(info.instance, 'shutdown'):
                try:
                    info.instance.shutdown()
                except Exception:
                    pass
            
            info.instance = None
            info.state = ModuleState.UNLOADED
            
            del self._modules[name]
            self._load_order.remove(name)
            
            return True
    
    # ─── Module Initialization ─────────────────────────────────────────────
    
    def init_module(self, name: str) -> bool:
        """Initialize a single module.
        
        Apollo equivalent: ModuleController::InitModule()
        """
        with self._lock:
            if name not in self._modules:
                return False
            
            info = self._modules[name]
            if info.state != ModuleState.LOADED:
                return False
            
            try:
                start_time = time.monotonic()
                
                if hasattr(info.instance, 'init'):
                    result = info.instance.init()
                    if result is False:
                        raise RuntimeError("init() returned False")
                elif hasattr(info.instance, 'Init'):
                    result = info.instance.Init()
                    if result is False:
                        raise RuntimeError("Init() returned False")
                
                info.init_time = time.monotonic() - start_time
                self._change_state(name, ModuleState.INITIALIZED)
                self._stats["total_initialized"] += 1
                return True
                
            except Exception as e:
                info.error_message = str(e)
                self._change_state(name, ModuleState.ERROR)
                self._stats["total_errors"] += 1
                return False
    
    def init_all(self) -> bool:
        """Initialize all loaded modules.
        
        Apollo equivalent: ModuleController::Init()
        """
        success = True
        for name in self._load_order:
            info = self._modules.get(name)
            if info and info.state == ModuleState.LOADED:
                if not self.init_module(name):
                    success = False
        return success
    
    # ─── Module Start/Stop ─────────────────────────────────────────────────
    
    def start_module(self, name: str) -> bool:
        """Start a single module."""
        with self._lock:
            if name not in self._modules:
                return False
            
            info = self._modules[name]
            if info.state != ModuleState.INITIALIZED:
                return False
            
            try:
                if hasattr(info.instance, 'start'):
                    info.instance.start()
                elif hasattr(info.instance, 'Start'):
                    info.instance.Start()
                
                self._change_state(name, ModuleState.RUNNING)
                self._stats["total_started"] += 1
                return True
                
            except Exception as e:
                info.error_message = str(e)
                self._change_state(name, ModuleState.ERROR)
                self._stats["total_errors"] += 1
                return False
    
    def stop_module(self, name: str) -> bool:
        """Stop a single module."""
        with self._lock:
            if name not in self._modules:
                return False
            
            info = self._modules[name]
            if info.state != ModuleState.RUNNING:
                return False
            
            try:
                if hasattr(info.instance, 'stop'):
                    info.instance.stop()
                elif hasattr(info.instance, 'Stop'):
                    info.instance.Stop()
                
                self._change_state(name, ModuleState.STOPPED)
                return True
                
            except Exception as e:
                info.error_message = str(e)
                self._change_state(name, ModuleState.ERROR)
                return False
    
    def start_all(self) -> bool:
        """Start all initialized modules."""
        success = True
        for name in self._load_order:
            info = self._modules.get(name)
            if info and info.state == ModuleState.INITIALIZED:
                if not self.start_module(name):
                    success = False
        return success
    
    def stop_all(self) -> bool:
        """Stop all running modules (in reverse order)."""
        success = True
        for name in reversed(self._load_order):
            info = self._modules.get(name)
            if info and info.state == ModuleState.RUNNING:
                if not self.stop_module(name):
                    success = False
        return success
    
    def shutdown(self) -> None:
        """Shutdown all modules and cleanup.
        
        Apollo equivalent: ModuleController::Clear()
        """
        self.stop_all()
        
        # Unload in reverse order
        for name in reversed(list(self._load_order)):
            self.unload_module(name)
    
    # ─── State Management ──────────────────────────────────────────────────
    
    def _change_state(self, name: str, new_state: ModuleState) -> None:
        """Change module state and notify callbacks."""
        info = self._modules.get(name)
        if info is None:
            return
        
        old_state = info.state
        info.state = new_state
        
        for callback in self._on_module_state_change:
            try:
                callback(name, old_state, new_state)
            except Exception:
                pass
    
    def on_state_change(
        self,
        callback: Callable[[str, ModuleState, ModuleState], None],
    ) -> None:
        """Register callback for state changes."""
        self._on_module_state_change.append(callback)
    
    # ─── Introspection ─────────────────────────────────────────────────────
    
    def get_module(self, name: str) -> Optional[Any]:
        """Get module instance by name."""
        with self._lock:
            info = self._modules.get(name)
            return info.instance if info else None
    
    def get_module_info(self, name: str) -> Optional[Dict]:
        """Get module info by name."""
        with self._lock:
            info = self._modules.get(name)
            return info.to_dict() if info else None
    
    def list_modules(self) -> List[str]:
        """List all module names."""
        with self._lock:
            return list(self._load_order)
    
    def stats(self) -> Dict:
        """Get controller statistics."""
        with self._lock:
            state_counts = {}
            for info in self._modules.values():
                state = info.state.name
                state_counts[state] = state_counts.get(state, 0) + 1
            
            return {
                **self._stats,
                "module_count": len(self._modules),
                "state_distribution": state_counts,
            }
