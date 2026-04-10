#!/usr/bin/env python3
"""
cyber/profiler/block_manager.py — Block Manager Alias
======================================================

This module re-exports BlockManager from block.py for backwards compatibility.

Apollo reference:
    cyber/profiler/block_manager.cc

位置: lolbot-HyperAI/cyber/profiler/block_manager.py
"""

from cyber.profiler.block import BlockManager

__all__ = ["BlockManager"]
