#!/usr/bin/env python3
"""
cyber/sysmo/sysmo.py — System Monitor
======================================

从 Apollo `cyber/sysmo/sysmo.cc` 这个好例子开始。然后, 遵循该模式实现
一个新的 `SysMo`, 让系统可以监控 CPU、内存、线程等系统资源。

Apollo reference:
    cyber/sysmo/sysmo.cc   — SysMo class
    cyber/sysmo/sysmo.h

位置: lolbot-HyperAI/cyber/sysmo/sysmo.py
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


class SystemHealth(Enum):
    """System health status."""
    HEALTHY = auto()
    WARNING = auto()
    CRITICAL = auto()
    UNKNOWN = auto()


@dataclass
class SysMonConfig:
    """Configuration for system monitor."""
    sample_interval_ms: float = 1000.0  # 1 second
    cpu_warning_threshold: float = 80.0
    cpu_critical_threshold: float = 95.0
    memory_warning_threshold: float = 80.0
    memory_critical_threshold: float = 95.0
    enable_process_monitor: bool = True
    enable_thread_monitor: bool = True


@dataclass
class SystemSnapshot:
    """Snapshot of system state at a point in time."""
    timestamp: float = 0.0
    
    # CPU
    cpu_percent: float = 0.0
    cpu_count: int = 0
    cpu_freq_mhz: float = 0.0
    
    # Memory
    memory_total_mb: float = 0.0
    memory_used_mb: float = 0.0
    memory_percent: float = 0.0
    
    # Process (current process)
    process_cpu_percent: float = 0.0
    process_memory_mb: float = 0.0
    process_threads: int = 0
    
    # Health
    health: SystemHealth = SystemHealth.UNKNOWN


class SysMo:
    """
    System Monitor for resource tracking.
    
    Apollo equivalent: cyber/sysmo/sysmo.cc
    
    Monitors:
    - CPU usage (system-wide and per-process)
    - Memory usage
    - Thread count
    - System health status
    
    Usage::
    
        sysmo = SysMo.instance()
        sysmo.start()
        
        # Get current snapshot
        snapshot = sysmo.snapshot()
        print(f"CPU: {snapshot.cpu_percent}%")
        
        # Register callback for health changes
        sysmo.on_health_change(my_callback)
        
        sysmo.stop()
    """
    
    _instance: Optional[SysMo] = None
    _instance_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> SysMo:
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
    
    def __init__(self, config: Optional[SysMonConfig] = None) -> None:
        self._config = config or SysMonConfig()
        
        self._running = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        
        # Current snapshot
        self._current_snapshot = SystemSnapshot()
        self._last_health = SystemHealth.UNKNOWN
        
        # Health change callbacks
        self._health_callbacks: List[Callable[[SystemHealth, SystemHealth], None]] = []
        
        # Statistics
        self._stats = {
            "sample_count": 0,
            "warning_count": 0,
            "critical_count": 0,
        }
        
        # Process handle
        self._process = None
        if HAS_PSUTIL:
            self._process = psutil.Process(os.getpid())
    
    # ─── Lifecycle ─────────────────────────────────────────────────────────
    
    def start(self) -> bool:
        """Start the system monitor thread.
        
        Apollo equivalent: SysMo::Start()
        """
        with self._lock:
            if self._running:
                return True
            
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._monitor_loop,
                name="sysmo",
                daemon=True,
            )
            self._thread.start()
            self._running = True
            return True
    
    def stop(self) -> None:
        """Stop the system monitor thread.
        
        Apollo equivalent: SysMo::Stop()
        """
        with self._lock:
            if not self._running:
                return
            
            self._stop_event.set()
            self._running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
    
    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        interval_s = self._config.sample_interval_ms / 1000.0
        
        while not self._stop_event.is_set():
            self._sample()
            self._stop_event.wait(timeout=interval_s)
    
    def _sample(self) -> None:
        """Take a system sample."""
        snapshot = self._collect_snapshot()
        
        with self._lock:
            old_health = self._last_health
            self._current_snapshot = snapshot
            self._last_health = snapshot.health
            self._stats["sample_count"] += 1
            
            if snapshot.health == SystemHealth.WARNING:
                self._stats["warning_count"] += 1
            elif snapshot.health == SystemHealth.CRITICAL:
                self._stats["critical_count"] += 1
        
        # Notify health change
        if old_health != snapshot.health:
            for callback in self._health_callbacks:
                try:
                    callback(old_health, snapshot.health)
                except Exception:
                    pass
    
    def _collect_snapshot(self) -> SystemSnapshot:
        """Collect system metrics."""
        snapshot = SystemSnapshot(timestamp=time.time())
        
        if not HAS_PSUTIL:
            snapshot.health = SystemHealth.UNKNOWN
            return snapshot
        
        try:
            # System CPU
            snapshot.cpu_percent = psutil.cpu_percent(interval=None)
            snapshot.cpu_count = psutil.cpu_count() or 1
            freq = psutil.cpu_freq()
            if freq:
                snapshot.cpu_freq_mhz = freq.current
            
            # System memory
            mem = psutil.virtual_memory()
            snapshot.memory_total_mb = mem.total / (1024 * 1024)
            snapshot.memory_used_mb = mem.used / (1024 * 1024)
            snapshot.memory_percent = mem.percent
            
            # Process stats
            if self._process and self._config.enable_process_monitor:
                snapshot.process_cpu_percent = self._process.cpu_percent()
                snapshot.process_memory_mb = (
                    self._process.memory_info().rss / (1024 * 1024)
                )
                if self._config.enable_thread_monitor:
                    snapshot.process_threads = self._process.num_threads()
            
            # Determine health
            snapshot.health = self._evaluate_health(snapshot)
            
        except Exception:
            snapshot.health = SystemHealth.UNKNOWN
        
        return snapshot
    
    def _evaluate_health(self, snapshot: SystemSnapshot) -> SystemHealth:
        """Evaluate system health based on thresholds."""
        # Check for critical conditions
        if (snapshot.cpu_percent >= self._config.cpu_critical_threshold or
            snapshot.memory_percent >= self._config.memory_critical_threshold):
            return SystemHealth.CRITICAL
        
        # Check for warning conditions
        if (snapshot.cpu_percent >= self._config.cpu_warning_threshold or
            snapshot.memory_percent >= self._config.memory_warning_threshold):
            return SystemHealth.WARNING
        
        return SystemHealth.HEALTHY
    
    # ─── API ───────────────────────────────────────────────────────────────
    
    def snapshot(self) -> SystemSnapshot:
        """Get current system snapshot."""
        with self._lock:
            return self._current_snapshot
    
    def health(self) -> SystemHealth:
        """Get current system health."""
        with self._lock:
            return self._last_health
    
    def on_health_change(
        self,
        callback: Callable[[SystemHealth, SystemHealth], None],
    ) -> None:
        """Register callback for health changes.
        
        Args:
            callback: Function(old_health, new_health)
        """
        self._health_callbacks.append(callback)
    
    def force_sample(self) -> SystemSnapshot:
        """Force an immediate sample."""
        snapshot = self._collect_snapshot()
        with self._lock:
            self._current_snapshot = snapshot
        return snapshot
    
    # ─── Introspection ─────────────────────────────────────────────────────
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    def stats(self) -> Dict:
        """Get monitor statistics."""
        with self._lock:
            snapshot = self._current_snapshot
            return {
                "running": self._running,
                "has_psutil": HAS_PSUTIL,
                **self._stats,
                "current_health": self._last_health.name,
                "cpu_percent": snapshot.cpu_percent,
                "memory_percent": snapshot.memory_percent,
                "process_threads": snapshot.process_threads,
            }
