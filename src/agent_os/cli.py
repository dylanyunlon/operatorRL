"""
Agent OS CLI - Command line interface for Agent OS

Usage:
    agentctl status          # Check installation status
    agentctl kernel start    # Start kernel (future)
    agentctl agent list      # List agents (future)
"""

import argparse
import sys


def main():
    """Main entry point for Agent OS CLI."""
    parser = argparse.ArgumentParser(
        prog="agentctl",
        description="Agent OS - A Safety-First Kernel for Autonomous AI Agents",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Check installation status")
    
    # Version command
    version_parser = subparsers.add_parser("version", help="Show version")
    
    # Parse args
    args = parser.parse_args()
    
    if args.command == "status":
        from agent_os import check_installation
        check_installation()
    elif args.command == "version":
        from agent_os import __version__
        print(f"Agent OS v{__version__}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
