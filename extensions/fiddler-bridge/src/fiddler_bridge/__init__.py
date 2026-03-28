"""
Fiddler Bridge — MCP-based async client for Fiddler Everywhere traffic capture.

This extension provides a production-grade bridge between Fiddler's MCP server
and the operatorRL agentic training pipeline.

Architecture:
    FiddlerBridgeClient → SessionCapturePipeline → protocol-decoder → AgentOS

Location: extensions/fiddler-bridge/src/fiddler_bridge/__init__.py
"""

from __future__ import annotations

__version__ = "0.1.0"

_EVOLUTION_KEY: str = "fiddler_bridge.mcp.v1"
_COMPUTE_BACKEND_DEFAULT: str = "cpu"
_MATURITY_GATE_FIDDLER: int = 0
