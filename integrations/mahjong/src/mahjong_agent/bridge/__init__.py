"""
Bridge subpackage — MITM bridge implementations for mahjong platforms.

Exports:
    MahjongBridgeBase: Abstract base class for all mahjong bridges
    MajsoulBridge: 雀魂 (Majsoul) bridge implementation
    LiqiParserAdapter: Protocol decoder delegation layer

Location: integrations/mahjong/src/mahjong_agent/bridge/__init__.py
"""

from mahjong_agent.bridge.bridge_base import MahjongBridgeBase

__all__ = ["MahjongBridgeBase"]
