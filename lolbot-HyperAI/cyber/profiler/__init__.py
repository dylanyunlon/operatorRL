"""
cyber/profiler — Apollo-style Performance Profiling
=====================================================

This module provides frame and block-level profiling for performance analysis.

Apollo reference: cyber/profiler/

Usage::

    from cyber.profiler import Profiler, profile_frame, profile_block
    
    profiler = Profiler.instance()
    
    with profiler.frame("main_loop"):
        with profiler.block("perception"):
            ...
    
    profiler.export_chrome_trace("trace.json")
"""

from cyber.profiler.block import Block, BlockManager
from cyber.profiler.frame import Frame, FrameManager
from cyber.profiler.profiler import (
    Profiler,
    ProfilerConfig,
    profile_frame,
    profile_block,
)

__all__ = [
    "Block",
    "BlockManager",
    "Frame",
    "FrameManager",
    "Profiler",
    "ProfilerConfig",
    "profile_frame",
    "profile_block",
]
