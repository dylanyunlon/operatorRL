#!/usr/bin/env python3
"""
cyber/profiler/block.py — Performance Block
=============================================

从 Apollo `cyber/profiler/block.cc` 这个好例子开始。然后, 遵循该模式实现
一个新的 `Block`, 让系统可以记录代码块的执行时间。

Apollo reference:
    cyber/profiler/block.cc   — Block class
    cyber/profiler/block.h

位置: lolbot-HyperAI/cyber/profiler/block.py
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from cyber.profiler.frame import Frame


@dataclass
class Block:
    """
    A performance block representing a timed code section.
    
    Apollo equivalent: cyber/profiler/block.cc
    
    Blocks are used for fine-grained profiling within frames.
    Each block records:
    - Start and end times
    - Parent frame (if any)
    - Thread ID
    """
    
    name: str = ""
    start_time_us: float = 0.0
    end_time_us: float = 0.0
    thread_id: int = 0
    frame: Optional[Frame] = None
    
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
            "start_time_us": self.start_time_us,
            "end_time_us": self.end_time_us,
            "duration_us": self.duration_us,
            "thread_id": self.thread_id,
            "frame_name": self.frame.name if self.frame else None,
        }


class BlockManager:
    """
    Manager for performance blocks.
    
    Apollo equivalent: cyber/profiler/block_manager.cc
    
    Tracks blocks across all threads and provides aggregation.
    """
    
    def __init__(self, max_blocks: int = 10000) -> None:
        self._max_blocks = max_blocks
        self._blocks: Deque[Block] = deque(maxlen=max_blocks)
        self._lock = threading.Lock()
        
        # Aggregated stats by block name
        self._aggregates: Dict[str, Dict] = {}
    
    def start_block(
        self,
        name: str,
        frame: Optional[Frame] = None,
    ) -> Block:
        """Start a new block.
        
        Args:
            name: Block name
            frame: Parent frame (if any)
        
        Returns:
            The created block
        """
        block = Block(
            name=name,
            start_time_us=time.monotonic() * 1e6,
            thread_id=threading.get_ident(),
            frame=frame,
        )
        return block
    
    def end_block(self, block: Block) -> None:
        """End a block and record it.
        
        Args:
            block: The block to end
        """
        block.end_time_us = time.monotonic() * 1e6
        
        with self._lock:
            self._blocks.append(block)
            self._update_aggregate(block)
    
    def _update_aggregate(self, block: Block) -> None:
        """Update aggregate statistics for a block name."""
        name = block.name
        duration = block.duration_us
        
        if name not in self._aggregates:
            self._aggregates[name] = {
                "count": 0,
                "total_us": 0.0,
                "min_us": float('inf'),
                "max_us": 0.0,
            }
        
        agg = self._aggregates[name]
        agg["count"] += 1
        agg["total_us"] += duration
        agg["min_us"] = min(agg["min_us"], duration)
        agg["max_us"] = max(agg["max_us"], duration)
    
    def get_aggregate(self, name: str) -> Optional[Dict]:
        """Get aggregate stats for a block name."""
        with self._lock:
            agg = self._aggregates.get(name)
            if agg is None:
                return None
            
            # Calculate average
            result = agg.copy()
            result["avg_us"] = (
                agg["total_us"] / agg["count"] if agg["count"] > 0 else 0.0
            )
            return result
    
    def to_list(self) -> List[Dict]:
        """Convert all blocks to list of dicts."""
        with self._lock:
            return [b.to_dict() for b in self._blocks]
    
    def clear(self) -> None:
        """Clear all blocks."""
        with self._lock:
            self._blocks.clear()
            self._aggregates.clear()
    
    def stats(self) -> Dict:
        """Get manager statistics."""
        with self._lock:
            return {
                "block_count": len(self._blocks),
                "max_blocks": self._max_blocks,
                "unique_names": len(self._aggregates),
                "aggregates": {
                    name: {
                        **agg,
                        "avg_us": agg["total_us"] / agg["count"] if agg["count"] > 0 else 0.0,
                    }
                    for name, agg in self._aggregates.items()
                },
            }
