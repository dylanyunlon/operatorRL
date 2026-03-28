"""
Protocol Decoder — Game protocol codec registry and implementations.

Provides GameCodec ABC, LiqiCodec (Majsoul), LoLCodec (League of Legends),
and extensible CodecRegistry for game protocol parsing.

Architecture:
    Raw bytes (from fiddler-bridge) → GameCodec.parse() → structured dict → AgentOS

Location: extensions/protocol-decoder/src/protocol_decoder/__init__.py
"""

from __future__ import annotations

__version__ = "0.1.0"

_EVOLUTION_KEY: str = "protocol_decoder.codec.v1"
_COMPUTE_BACKEND_DEFAULT: str = "cpu"
_MATURITY_GATE_PROTOCOL: int = 0
