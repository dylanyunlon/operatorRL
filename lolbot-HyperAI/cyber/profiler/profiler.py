#!/usr/bin/env python3
"""
cyber/profiler/profiler.py — Apollo-style Profiler
====================================================

从 Apollo `cyber/profiler/profiler.h` 这个好例子开始。然后, 遵循该模式实现
一个新的 `Profiler`, 让系统可以进行 per-frame 性能分析, 并能生成火焰图数据。

Apollo reference:
    cyber/profiler/profiler.h   — PERF_BLOCK_START/END macros
    cyber/profiler/block.cc     — Block class

位置: lolbot-HyperAI/cyber/profiler/profiler.py
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Generator, List, Optional

from cyber.profiler.block import Block, BlockManager
from cyber.profiler.frame import Frame, FrameManager


@dataclass
class ProfilerConfig:
    """Configuration for profiler."""
    enabled: bool = True
    max_frames: int = 1000
    output_dir: str = "logs/profiler"
    auto_export_interval_s: float = 60.0


class Profiler:
    """
    Performance profiler with frame and block tracking.
    
    Apollo equivalent: cyber/profiler/profiler.h + macros
    
    The profiler tracks execution time at two levels:
    1. Frames: High-level execution cycles (e.g., one Proc() call)
    2. Blocks: Fine-grained sections within frames
    
    Usage::
    
        profiler = Profiler.instance()
        
        # Track a frame
        with profiler.frame("perception"):
            with profiler.block("preprocess"):
                # ... preprocessing
            with profiler.block("inference"):
                # ... inference
        
        # Export data
        profiler.export_json("profile.json")
    """
    
    _instance: Optional[Profiler] = None
    _instance_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> Profiler:
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
    
    def __init__(self, config: Optional[ProfilerConfig] = None) -> None:
        self._config = config or ProfilerConfig()
        self._enabled = self._config.enabled
        
        self._frame_manager = FrameManager(max_frames=self._config.max_frames)
        self._block_manager = BlockManager()
        
        self._lock = threading.RLock()
        self._thread_local = threading.local()
        
        # Statistics
        self._stats = {
            "total_frames": 0,
            "total_blocks": 0,
        }
    
    # ─── Frame Tracking ────────────────────────────────────────────────────
    
    @contextlib.contextmanager
    def frame(self, name: str) -> Generator[Frame, None, None]:
        """Context manager for frame profiling.
        
        Apollo equivalent: PERF_FRAME_START/END
        
        Usage::
        
            with profiler.frame("perception"):
                # Frame code here
        """
        if not self._enabled:
            yield Frame(name=name)  # Dummy frame
            return
        
        f = self._frame_manager.start_frame(name)
        self._get_stack().append(f)
        
        try:
            yield f
        finally:
            self._get_stack().pop()
            self._frame_manager.end_frame(f)
            with self._lock:
                self._stats["total_frames"] += 1
    
    # ─── Block Tracking ────────────────────────────────────────────────────
    
    @contextlib.contextmanager
    def block(self, name: str) -> Generator[Block, None, None]:
        """Context manager for block profiling.
        
        Apollo equivalent: PERF_BLOCK_START/END
        
        Usage::
        
            with profiler.block("inference"):
                # Block code here
        """
        if not self._enabled:
            yield Block(name=name)  # Dummy block
            return
        
        # Get current frame
        stack = self._get_stack()
        current_frame = stack[-1] if stack else None
        
        b = self._block_manager.start_block(name, frame=current_frame)
        
        try:
            yield b
        finally:
            self._block_manager.end_block(b)
            with self._lock:
                self._stats["total_blocks"] += 1
    
    def _get_stack(self) -> List[Frame]:
        """Get per-thread frame stack."""
        if not hasattr(self._thread_local, 'stack'):
            self._thread_local.stack = []
        return self._thread_local.stack
    
    # ─── Control ───────────────────────────────────────────────────────────
    
    def enable(self) -> None:
        """Enable profiling."""
        self._enabled = True
    
    def disable(self) -> None:
        """Disable profiling."""
        self._enabled = False
    
    def clear(self) -> None:
        """Clear all profiling data."""
        self._frame_manager.clear()
        self._block_manager.clear()
    
    # ─── Export ────────────────────────────────────────────────────────────
    
    def export_json(self, path: Optional[str] = None) -> str:
        """Export profiling data to JSON.
        
        Args:
            path: Output file path (auto-generated if None)
        
        Returns:
            Path to the exported file
        """
        if path is None:
            output_dir = Path(self._config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            path = str(output_dir / f"profile_{int(time.time())}.json")
        
        data = {
            "timestamp": time.time(),
            "stats": self._stats.copy(),
            "frames": self._frame_manager.to_list(),
            "blocks": self._block_manager.to_list(),
        }
        
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        
        return path
    
    def export_chrome_trace(self, path: Optional[str] = None) -> str:
        """Export in Chrome trace format for visualization.
        
        The output can be viewed in chrome://tracing
        
        Args:
            path: Output file path (auto-generated if None)
        
        Returns:
            Path to the exported file
        """
        if path is None:
            output_dir = Path(self._config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            path = str(output_dir / f"trace_{int(time.time())}.json")
        
        events = []
        
        # Convert frames to trace events
        for frame in self._frame_manager.to_list():
            events.append({
                "name": frame["name"],
                "cat": "frame",
                "ph": "X",  # Complete event
                "ts": frame["start_time_us"],
                "dur": frame["duration_us"],
                "pid": 1,
                "tid": frame.get("thread_id", 1),
            })
        
        # Convert blocks to trace events
        for block in self._block_manager.to_list():
            events.append({
                "name": block["name"],
                "cat": "block",
                "ph": "X",
                "ts": block["start_time_us"],
                "dur": block["duration_us"],
                "pid": 1,
                "tid": block.get("thread_id", 1),
            })
        
        with open(path, 'w') as f:
            json.dump({"traceEvents": events}, f)
        
        return path
    
    # ─── Introspection ─────────────────────────────────────────────────────
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    def stats(self) -> Dict:
        """Get profiler statistics."""
        with self._lock:
            return {
                "enabled": self._enabled,
                **self._stats,
                "frame_stats": self._frame_manager.stats(),
                "block_stats": self._block_manager.stats(),
            }


# ─── Convenience Functions ─────────────────────────────────────────────────

def profile_frame(name: str):
    """Decorator for profiling a function as a frame."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            with Profiler.instance().frame(name):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def profile_block(name: str):
    """Decorator for profiling a function as a block."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            with Profiler.instance().block(name):
                return func(*args, **kwargs)
        return wrapper
    return decorator
