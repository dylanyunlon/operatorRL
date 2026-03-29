"""
Fiddler CLI Integration — CLI subcommands + status display + config.

Provides argparse subcommands for Fiddler MCP operations:
start, stop, status. Integrates with existing CLI framework.

Location: extensions/fiddler-bridge/src/fiddler_cli_integration.py

Reference (拿来主义):
  - agentos/cli/main.py: CLI argparse + route pattern
  - agentos/cli/status_reporter.py: status formatting
  - Fiddler MCP Server documentation: command structure
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_cli_integration.v1"

# Internal simulated state (production would connect to real Fiddler)
_sim_state: dict[str, Any] = {"state": "idle", "host": "", "port": 0, "packets_captured": 0}


def create_fiddler_parser() -> argparse.ArgumentParser:
    """Create argparse parser for Fiddler CLI subcommands.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="agentos-fiddler",
        description="Fiddler MCP Bridge CLI",
    )
    sub = parser.add_subparsers(dest="command")

    start_p = sub.add_parser("start", help="Start Fiddler capture session")
    start_p.add_argument("--host", default="localhost", help="Fiddler host")
    start_p.add_argument("--port", type=int, default=8866, help="Fiddler port")

    sub.add_parser("stop", help="Stop Fiddler capture session")
    sub.add_parser("status", help="Show Fiddler session status")

    return parser


def route_fiddler_command(command: str, **kwargs: Any) -> dict[str, Any]:
    """Route a Fiddler CLI command to the appropriate handler.

    Args:
        command: Command name (start, stop, status).
        **kwargs: Command-specific arguments.

    Returns:
        Result dict with status and details.
    """
    if command == "start":
        host = kwargs.get("host", "localhost")
        port = kwargs.get("port", 8866)
        _sim_state["state"] = "capturing"
        _sim_state["host"] = host
        _sim_state["port"] = port
        logger.info("Fiddler CLI: started capture on %s:%d", host, port)
        return {"status": "started", "host": host, "port": port}

    elif command == "stop":
        _sim_state["state"] = "idle"
        logger.info("Fiddler CLI: stopped capture")
        return {"status": "stopped"}

    elif command == "status":
        return {
            "status": "ok",
            "state": _sim_state["state"],
            "host": _sim_state["host"],
            "port": _sim_state["port"],
            "packets_captured": _sim_state["packets_captured"],
        }

    else:
        return {"status": "unknown", "error": f"Unknown command: {command}"}


def format_fiddler_status(status_data: dict[str, Any]) -> str:
    """Format Fiddler status for CLI display.

    Args:
        status_data: Status dict with state, host, port, packets_captured.

    Returns:
        Formatted status string.
    """
    state = status_data.get("state", "unknown")
    host = status_data.get("host", "")
    port = status_data.get("port", 0)
    packets = status_data.get("packets_captured", 0)

    lines = [
        f"Fiddler MCP Bridge Status",
        f"  State: {state}",
        f"  Host: {host}:{port}" if host else "  Host: (not connected)",
        f"  Packets Captured: {packets}",
    ]
    return "\n".join(lines)
