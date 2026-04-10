#!/usr/bin/env python3
"""
cyber/profiler/frame.py — Performance Frame
=============================================

从 Apollo `cyber/profiler/frame.h` 这个好例子开始。然后, 遵循该模式实现
一个新的 `Frame`, 让系统可以记录每帧的执行时间和统计。

Apollo reference:
    cyber/profiler/profiler.h   — PERF_FRAME macros

位置: lolbot-HyperAI/cyber/profiler/frame.py
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class Frame:
    """
    A performance frame representing one execution cycle.
    
    Apollo equivalent: High-level profiling for Proc() calls
    
    Frames are used for coarse-grained profiling of entire
    execution cycles (e.g., one call to Component::Proc()).
    """
    
    name: str = ""
    frame_id: int = 0
    start_time_us: float = 0.0
    end_time_us: float = 0.0
    thread_id: int = 0
    
    # Nested block count
    block_count: int = 0
    
    @property
    def duration_us(self) -> float:
        """Duration in microseconds."""
        if self.end_time_us <= 0:
            return 0.0
        return self.end_time_us - self.start_time_us
    
    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds."""
        return self.duration_us / 1000.0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "frame_id": self.frame_id,
            "start_time_us": self.start_time_us,
            "end_time_us": self.end_time_us,
            "duration_us": self.duration_us,
            "thread_id": self.thread_id,
            "block_count": self.block_count,
        }


class FrameManager:
    """
    Manager for performance frames.
    
    Tracks frames across all threads and provides statistics.
    """
    
    def __init__(self, max_frames: int = 1000) -> None:
        self._max_frames = max_frames
        self._frames: Deque[Frame] = deque(maxlen=max_frames)
        self._lock = threading.Lock()
        self._frame_counter = 0
        
        # Aggregated stats by frame name
        self._aggregates: Dict[str, Dict] = {}
    
    def start_frame(self, name: str) -> Frame:
        """Start a new frame.
        
        Args:
            name: Frame name (e.g., "perception", "planning")
        
        Returns:
            The created frame
        """
        with self._lock:
            self._frame_counter += 1
            frame_id = self._frame_counter
        
        frame = Frame(
            name=name,
            frame_id=frame_id,
            start_time_us=time.monotonic() * 1e6,
            thread_id=threading.get_ident(),
        )
        return frame
    
    def end_frame(self, frame: Frame) -> None:
        """End a frame and record it.
        
        Args:
            frame: The frame to end
        """
        frame.end_time_us = time.monotonic() * 1e6
        
        with self._lock:
            self._frames.append(frame)
            self._update_aggregate(frame)
    
    def _update_aggregate(self, frame: Frame) -> None:
        """Update aggregate statistics for a frame name."""
        name = frame.name
        duration = frame.duration_us
        
        if name not in self._aggregates:
            self._aggregates[name] = {
                "count": 0,
                "total_us": 0.0,
                "min_us": float('inf'),
                "max_us": 0.0,
                "recent_durations": deque(maxlen=100),
            }
        
        agg = self._aggregates[name]
        agg["count"] += 1
        agg["total_us"] += duration
        agg["min_us"] = min(agg["min_us"], duration)
        agg["max_us"] = max(agg["max_us"], duration)
        agg["recent_durations"].append(duration)
    
    def get_aggregate(self, name: str) -> Optional[Dict]:
        """Get aggregate stats for a frame name."""
        with self._lock:
            agg = self._aggregates.get(name)
            if agg is None:
                return None
            
            count = agg["count"]
            recent = list(agg["recent_durations"])
            
            result = {
                "count": count,
                "total_us": agg["total_us"],
                "min_us": agg["min_us"],
                "max_us": agg["max_us"],
                "avg_us": agg["total_us"] / count if count > 0 else 0.0,
            }
            
            # Calculate percentiles from recent samples
            if recent:
                recent.sort()
                n = len(recent)
                result["p50_us"] = recent[n // 2]
                result["p90_us"] = recent[int(n * 0.9)]
                result["p99_us"] = recent[int(n * 0.99)] if n >= 100 else recent[-1]
            
            return result
    
    def to_list(self) -> List[Dict]:
        """Convert all frames to list of dicts."""
        with self._lock:
            return [f.to_dict() for f in self._frames]
    
    def clear(self) -> None:
        """Clear all frames."""
        with self._lock:
            self._frames.clear()
            self._aggregates.clear()
            self._frame_counter = 0
    
    def stats(self) -> Dict:
        """Get manager statistics."""
        with self._lock:
            aggregates = {}
            for name, agg in self._aggregates.items():
                count = agg["count"]
                aggregates[name] = {
                    "count": count,
                    "avg_us": agg["total_us"] / count if count > 0 else 0.0,
                    "min_us": agg["min_us"],
                    "max_us": agg["max_us"],
                }
            
            return {
                "frame_count": len(self._frames),
                "max_frames": self._max_frames,
                "unique_names": len(self._aggregates),
                "aggregates": aggregates,
            }
