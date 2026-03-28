"""
Mahjong Agent — Agentic mahjong AI integration for operatorRL.

Bridges Akagi's MITM architecture with operatorRL's self-evolving loop.
Supports Majsoul (雀魂), Tenhou (天凤), and Riichi City (一番街).

Architecture:
    Fiddler Bridge → Protocol Decoder → MahjongAgent → MjaiBotBase → AgentOS

Location: integrations/mahjong/src/mahjong_agent/__init__.py
"""

from __future__ import annotations

__version__ = "0.1.0"

_EVOLUTION_KEY: str = "mahjong_agent.bridge.v1"
_COMPUTE_BACKEND_DEFAULT: str = "cpu"
_MATURITY_GATE_MAHJONG: int = 0
