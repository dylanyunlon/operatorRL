"""
CLI Main - Command-line interface for the LoL Fiddler Agent.

Provides subcommands for:
  - monitor : Start real-time game monitoring
  - replay  : Play back recorded sessions
  - export  : Export game data to CSV/JSON
  - config  : Manage configuration
  - status  : Check Fiddler and agent status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

from lol_fiddler_agent.utils.config import AppConfig, load_config
from lol_fiddler_agent.utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="lol-agent",
        description="League of Legends AI Strategy Agent with Fiddler MCP",
    )
    parser.add_argument(
        "-c", "--config", type=str, default=None,
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--log-level", type=str, default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level override",
    )
    parser.add_argument(
        "--json-logs", action="store_true",
        help="Use JSON-structured log output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Start game monitoring")
    monitor_parser.add_argument("--api-key", type=str, help="Fiddler API key")
    monitor_parser.add_argument("--host", type=str, default="localhost")
    monitor_parser.add_argument("--port", type=int, default=8868)
    monitor_parser.add_argument("--poll-interval", type=float, default=2.0)
    monitor_parser.add_argument("--no-record", action="store_true", help="Disable replay recording")

    # replay command
    replay_parser = subparsers.add_parser("replay", help="Replay recorded session")
    replay_parser.add_argument("file", type=str, help="Replay file path")
    replay_parser.add_argument("--speed", type=float, default=1.0, help="Playback speed")

    # export command
    export_parser = subparsers.add_parser("export", help="Export game data")
    export_parser.add_argument("--format", choices=["csv", "jsonl", "parquet"], default="csv")
    export_parser.add_argument("--output", type=str, default="./exports")
    export_parser.add_argument("--replay-dir", type=str, default="./replays")

    # status command
    subparsers.add_parser("status", help="Check system status")

    # config command
    config_parser = subparsers.add_parser("config", help="Manage configuration")
    config_parser.add_argument("--init", action="store_true", help="Create default config file")
    config_parser.add_argument("--show", action="store_true", help="Show current config")

    return parser


async def cmd_monitor(args: argparse.Namespace, config: AppConfig) -> int:
    """Run the monitoring command."""
    from lol_fiddler_agent.orchestrator import Orchestrator, OrchestratorConfig

    orch_config = OrchestratorConfig(
        fiddler_api_key=args.api_key or config.fiddler_api_key,
        fiddler_host=args.host or config.fiddler_host,
        fiddler_port=args.port or config.fiddler_port,
        poll_interval=args.poll_interval or config.poll_interval,
        enable_recording=not args.no_record,
    )

    orch = Orchestrator(orch_config)

    try:
        await orch.start()
        print("LoL Strategy Agent running. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping agent...")
        summary = await orch.stop()
        print(f"Session summary: {summary}")

    return 0


async def cmd_replay(args: argparse.Namespace, config: AppConfig) -> int:
    """Run the replay command."""
    from lol_fiddler_agent.replay.recorder import ReplayPlayer

    player = ReplayPlayer()
    if not player.load(args.file):
        print(f"Failed to load replay: {args.file}")
        return 1

    meta = player.metadata
    if meta:
        print(f"Replay: {meta.replay_id}")
        print(f"Champion: {meta.champion}")
        print(f"Duration: {meta.duration_seconds:.0f}s")
        print(f"Events: {meta.event_count}")

    for event in player.events():
        print(f"[{event.timestamp:6.1f}s] {event.event_type}: {str(event.data)[:100]}")
        await asyncio.sleep(0.1 / args.speed)

    return 0


async def cmd_status(args: argparse.Namespace, config: AppConfig) -> int:
    """Run the status command."""
    from lol_fiddler_agent.network.fiddler_client import FiddlerConfig, FiddlerMCPClient

    print("=== LoL Fiddler Agent Status ===\n")

    # Check Fiddler
    fiddler_config = FiddlerConfig(
        host=config.fiddler_host,
        port=config.fiddler_port,
        api_key=config.fiddler_api_key,
    )
    print(f"Fiddler MCP: {fiddler_config.base_url}")
    try:
        async with FiddlerMCPClient(fiddler_config) as client:
            status = await client.get_status()
            print(f"  Status: Connected")
            print(f"  Sessions: {await client.get_sessions_count()}")
    except Exception as e:
        print(f"  Status: DISCONNECTED ({e})")

    # Check Live Client API
    print(f"\nLoL Live Client API: https://127.0.0.1:2999")
    try:
        import httpx
        async with httpx.AsyncClient(verify=False, timeout=3) as http:
            resp = await http.get("https://127.0.0.1:2999/liveclientdata/gamestats")
            if resp.status_code == 200:
                print(f"  Status: IN GAME")
            else:
                print(f"  Status: Not in game (HTTP {resp.status_code})")
    except Exception:
        print(f"  Status: Not running")

    print(f"\nConfig: {config.agent_id}")
    print(f"Replay dir: {config.replay_dir}")
    print(f"Training dir: {config.training_data_dir}")

    return 0


async def cmd_config(args: argparse.Namespace, config: AppConfig) -> int:
    """Manage configuration."""
    if args.init:
        from lol_fiddler_agent.utils.config import save_config
        save_config(config, "config/settings.yaml")
        print("Created config/settings.yaml")
    elif args.show:
        from dataclasses import asdict
        import json
        print(json.dumps(asdict(config), indent=2, default=str))
    return 0


def main() -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # Load config
    cli_overrides: dict[str, Any] = {}
    if args.log_level:
        cli_overrides["log_level"] = args.log_level

    config = load_config(args.config, cli_overrides)
    setup_logging(level=config.log_level, json_output=args.json_logs)

    if not args.command:
        parser.print_help()
        return 0

    # Dispatch command
    cmd_map = {
        "monitor": cmd_monitor,
        "replay": cmd_replay,
        "status": cmd_status,
        "config": cmd_config,
    }

    handler = cmd_map.get(args.command)
    if handler:
        return asyncio.run(handler(args, config))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
