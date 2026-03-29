"""
CLI Main — argparse command-line entry point with subcommand routing.

Provides unified CLI interface for starting, stopping, and monitoring
game AI agents across all supported games.

Location: agentos/cli/main.py

Reference (拿来主义):
  - Akagi/mjai_bot/controller.py: Controller dispatch pattern
  - PARL/parl/utils/communication.py: CLI argument handling
  - DI-star/distar/bin/play.py: argparse game selection
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

logger = logging.getLogger(__name__)

_VERSION: str = "0.6.0"


def get_version() -> str:
    """Return current AgentOS version string."""
    return _VERSION


def create_parser() -> argparse.ArgumentParser:
    """Create the main CLI argument parser with subcommands.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="agentos",
        description="AgentOS — Unified AI Game Agent Platform",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_VERSION}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- start ---
    start_p = subparsers.add_parser("start", help="Start a game agent")
    start_p.add_argument("--game", required=True, help="Game identifier (lol, dota2, mahjong)")
    start_p.add_argument("--config", default=None, help="Path to config file")
    start_p.add_argument("--debug", action="store_true", help="Enable debug mode")

    # --- stop ---
    stop_p = subparsers.add_parser("stop", help="Stop a running game agent")
    stop_p.add_argument("--game", required=True, help="Game identifier")

    # --- status ---
    subparsers.add_parser("status", help="Show status of all agents")

    # --- config ---
    config_p = subparsers.add_parser("config", help="Manage configuration")
    config_p.add_argument("--show", action="store_true", help="Show current config")

    # --- logs ---
    logs_p = subparsers.add_parser("logs", help="View agent logs")
    logs_p.add_argument("--game", default=None, help="Filter by game")
    logs_p.add_argument("--lines", type=int, default=50, help="Number of lines")

    return parser


def route_command(command: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Route a parsed command to its handler.

    Args:
        command: Command name string.
        kwargs: Command arguments dict.

    Returns:
        Result dict with 'status' key.
    """
    if command == "start":
        game = kwargs.get("game", "unknown")
        logger.info("Starting agent for game: %s", game)
        return {"status": "routed", "command": "start", "game": game}

    if command == "stop":
        game = kwargs.get("game", "unknown")
        logger.info("Stopping agent for game: %s", game)
        return {"status": "routed", "command": "stop", "game": game}

    if command == "status":
        return {"status": "routed", "command": "status", "agents": []}

    if command == "config":
        return {"status": "routed", "command": "config"}

    if command == "logs":
        return {"status": "routed", "command": "logs"}

    return {"status": "unknown", "error": f"Unknown command: {command}"}
