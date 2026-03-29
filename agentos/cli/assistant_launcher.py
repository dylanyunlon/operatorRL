"""
Assistant Launcher — Unified real-time assistant launch/manage CLI.

Provides argparse subcommands for launching, stopping, and listing
game-specific real-time assistants across all supported games.

Location: agentos/cli/assistant_launcher.py

Reference (拿来主义):
  - agentos/cli/main.py: CLI argparse + route pattern
  - agentos/cli/game_launcher.py: game register/launch/stop lifecycle
  - extensions/fiddler-bridge/src/fiddler_cli_integration.py: CLI integration
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.cli.assistant_launcher.v1"

# Simulated assistant state (production: real process management)
_active_assistants: dict[str, dict[str, Any]] = {}

# Supported games
_SUPPORTED_GAMES = ["lol", "dota2", "mahjong"]


def create_assistant_parser() -> argparse.ArgumentParser:
    """Create argparse parser for assistant launcher CLI.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="agentos-assistant",
        description="Real-Time Game Assistant Launcher",
    )
    sub = parser.add_subparsers(dest="command")

    launch_p = sub.add_parser("launch", help="Launch a game assistant")
    launch_p.add_argument("--game", required=True, help="Game identifier")

    stop_p = sub.add_parser("stop", help="Stop a game assistant")
    stop_p.add_argument("--game", required=True, help="Game identifier")

    sub.add_parser("list", help="List available/active assistants")

    return parser


def route_assistant_command(command: str, **kwargs: Any) -> dict[str, Any]:
    """Route an assistant CLI command.

    Args:
        command: Command name (launch, stop, list).
        **kwargs: Command-specific arguments.

    Returns:
        Result dict.
    """
    if command == "launch":
        game = kwargs.get("game", "")
        _active_assistants[game] = {
            "state": "running",
            "started_at": time.time(),
        }
        logger.info("Assistant launched for: %s", game)
        return {"status": "launched", "game": game}

    elif command == "stop":
        game = kwargs.get("game", "")
        _active_assistants.pop(game, None)
        logger.info("Assistant stopped for: %s", game)
        return {"status": "stopped", "game": game}

    elif command == "list":
        return {
            "status": "ok",
            "assistants": list(_active_assistants.keys()),
            "games": _SUPPORTED_GAMES,
            "active_count": len(_active_assistants),
        }

    else:
        return {"status": "unknown", "error": f"Unknown command: {command}"}


def format_assistant_status(status_data: dict[str, Any]) -> str:
    """Format assistant status for CLI display.

    Args:
        status_data: Status dict with game, state, uptime.

    Returns:
        Formatted status string.
    """
    game = status_data.get("game", "unknown")
    state = status_data.get("state", "unknown")
    uptime = status_data.get("uptime", 0.0)

    lines = [
        f"Game Assistant Status",
        f"  Game: {game}",
        f"  State: {state}",
        f"  Uptime: {uptime:.1f}s",
    ]
    return "\n".join(lines)
