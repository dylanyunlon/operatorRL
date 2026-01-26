"""
Agent OS CLI - Command line interface for Agent OS

Usage:
    agentos init [--template TEMPLATE]     Initialize .agents/ directory
    agentos secure [--policy POLICY]       Enable kernel governance
    agentos audit [--format FORMAT]        Audit agent security
    agentos status                         Show kernel status
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def cmd_init(args):
    """Initialize .agents/ directory with Agent OS support."""
    root = Path(args.path or ".")
    agents_dir = root / ".agents"
    
    if agents_dir.exists() and not args.force:
        print(f"Error: {agents_dir} already exists. Use --force to overwrite.")
        return 1
    
    agents_dir.mkdir(parents=True, exist_ok=True)
    
    # Create agents.md (OpenAI/Anthropic standard)
    agents_md = agents_dir / "agents.md"
    agents_md.write_text("""# Agent Configuration

You are an AI agent governed by Agent OS kernel.

## Capabilities

You can:
- Query databases (read-only by default)
- Call approved APIs
- Generate reports

## Constraints

You must:
- Follow all policies in security.md
- Request approval for write operations
- Log all actions to the flight recorder

## Context

This agent is part of the Agent OS ecosystem.
For more information: https://github.com/imran-siddique/agent-os
""")
    
    # Create security.md (Agent OS extension)
    security_md = agents_dir / "security.md"
    policy_template = args.template or "strict"
    
    policies = {
        "strict": {
            "mode": "strict",
            "signals": ["SIGSTOP", "SIGKILL", "SIGINT"],
            "rules": [
                {"action": "database_query", "mode": "read_only"},
                {"action": "file_write", "requires_approval": True},
                {"action": "api_call", "rate_limit": "100/hour"},
                {"action": "send_email", "requires_approval": True},
            ]
        },
        "permissive": {
            "mode": "permissive",
            "signals": ["SIGSTOP", "SIGKILL"],
            "rules": [
                {"action": "*", "effect": "allow"},
            ]
        },
        "audit": {
            "mode": "audit",
            "signals": ["SIGSTOP"],
            "rules": [
                {"action": "*", "effect": "allow", "log": True},
            ]
        }
    }
    
    policy = policies.get(policy_template, policies["strict"])
    
    security_content = f"""# Agent OS Security Configuration

kernel:
  version: "1.0"
  mode: {policy["mode"]}
  
signals:
"""
    for s in policy["signals"]:
        security_content += f"  - {s}\n"
    
    security_content += "\npolicies:\n"
    for r in policy["rules"]:
        security_content += f'  - action: {r["action"]}\n'
        if "mode" in r:
            security_content += f'    mode: {r["mode"]}\n'
        if r.get("requires_approval"):
            security_content += f'    requires_approval: true\n'
        if "rate_limit" in r:
            security_content += f'    rate_limit: "{r["rate_limit"]}"\n'
        if "effect" in r:
            security_content += f'    effect: {r["effect"]}\n'
    
    security_content += """
observability:
  metrics: true
  traces: true
  flight_recorder: true

# For more options, see:
# https://github.com/imran-siddique/agent-os/blob/main/docs/security-spec.md
"""
    
    security_md.write_text(security_content)
    
    print(f"Initialized Agent OS in {agents_dir}")
    print(f"  - agents.md: Agent instructions (OpenAI/Anthropic standard)")
    print(f"  - security.md: Kernel policies (Agent OS extension)")
    print(f"  - Template: {policy_template}")
    print()
    print("Next steps:")
    print("  1. Edit .agents/agents.md with your agent's capabilities")
    print("  2. Customize .agents/security.md policies")
    print("  3. Run: agentos secure --verify")
    
    return 0


def cmd_secure(args):
    """Enable kernel governance for the current directory."""
    root = Path(args.path or ".")
    agents_dir = root / ".agents"
    
    if not agents_dir.exists():
        print(f"Error: No .agents/ directory found. Run 'agentos init' first.")
        return 1
    
    security_md = agents_dir / "security.md"
    if not security_md.exists():
        print(f"Error: No security.md found. Run 'agentos init' first.")
        return 1
    
    print(f"Securing agents in {root}...")
    print()
    
    content = security_md.read_text()
    
    checks = [
        ("kernel version", "version:" in content),
        ("signals defined", "signals:" in content),
        ("policies defined", "policies:" in content),
    ]
    
    all_passed = True
    for check_name, passed in checks:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status} {check_name}")
        if not passed:
            all_passed = False
    
    print()
    
    if all_passed:
        print("Security configuration valid.")
        print()
        print("Kernel governance enabled. Your agents will now:")
        print("  - Enforce policies on every action")
        print("  - Respond to POSIX-style signals")
        print("  - Log all operations to flight recorder")
        return 0
    else:
        print("Security configuration invalid. Please fix the issues above.")
        return 1


def cmd_audit(args):
    """Audit agent security configuration."""
    root = Path(args.path or ".")
    agents_dir = root / ".agents"
    
    if not agents_dir.exists():
        print(f"No .agents/ directory found in {root}")
        return 1
    
    print(f"Auditing {root}...")
    print()
    
    files = {
        "agents.md": agents_dir / "agents.md",
        "security.md": agents_dir / "security.md",
    }
    
    findings = []
    
    for name, path in files.items():
        if path.exists():
            print(f"  [OK] {name}")
        else:
            print(f"  [MISSING] {name}")
            findings.append(f"Missing {name}")
    
    print()
    
    security_md = files["security.md"]
    if security_md.exists():
        content = security_md.read_text()
        
        dangerous = [
            ("effect: allow", "Permissive allow - consider adding constraints"),
        ]
        
        for pattern, warning in dangerous:
            if pattern in content and "action: *" in content:
                findings.append(f"Warning: {warning}")
        
        required = ["kernel:", "signals:", "policies:"]
        for section in required:
            if section not in content:
                findings.append(f"Missing required section: {section}")
    
    if findings:
        print("Findings:")
        for f in findings:
            print(f"  - {f}")
    else:
        print("No issues found.")
    
    print()
    
    if args.format == "json":
        result = {
            "path": str(root),
            "files": {name: path.exists() for name, path in files.items()},
            "findings": findings,
            "passed": len(findings) == 0
        }
        print(json.dumps(result, indent=2))
    
    return 0 if len(findings) == 0 else 1


def cmd_status(args):
    """Show kernel status."""
    print("Agent OS Kernel Status")
    print("=" * 40)
    print()
    
    try:
        import agent_os
        print(f"  Version: {agent_os.__version__}")
        print(f"  Status: Installed")
    except ImportError:
        print(f"  Status: Not installed")
        print()
        print("Install with: pip install agent-os-kernel")
        return 1
    
    print()
    
    root = Path(".")
    agents_dir = root / ".agents"
    
    if agents_dir.exists():
        print(f"  Project: {root.absolute()}")
        print(f"  Agents: Configured (.agents/ found)")
    else:
        print(f"  Project: {root.absolute()}")
        print(f"  Agents: Not configured")
        print()
        print("Initialize with: agentos init")
    
    print()
    
    print("Packages:")
    try:
        from agent_os import AVAILABLE_PACKAGES
        for pkg, available in AVAILABLE_PACKAGES.items():
            status = "installed" if available else "not installed"
            print(f"  - {pkg}: {status}")
    except:
        print("  Unable to check packages")
    
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="agentos",
        description="Agent OS CLI - Kernel-level governance for AI agents"
    )
    parser.add_argument(
        "--version", "-v",
        action="store_true",
        help="Show version"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # init command
    init_parser = subparsers.add_parser("init", help="Initialize .agents/ directory")
    init_parser.add_argument("--path", "-p", help="Path to initialize (default: current)")
    init_parser.add_argument("--template", "-t", choices=["strict", "permissive", "audit"],
                            default="strict", help="Policy template")
    init_parser.add_argument("--force", "-f", action="store_true", help="Overwrite existing")
    
    # secure command
    secure_parser = subparsers.add_parser("secure", help="Enable kernel governance")
    secure_parser.add_argument("--path", "-p", help="Path to secure (default: current)")
    secure_parser.add_argument("--verify", action="store_true", help="Verify only")
    
    # audit command
    audit_parser = subparsers.add_parser("audit", help="Audit security configuration")
    audit_parser.add_argument("--path", "-p", help="Path to audit (default: current)")
    audit_parser.add_argument("--format", "-f", choices=["text", "json"], default="text")
    
    # status command
    subparsers.add_parser("status", help="Show kernel status")
    
    args = parser.parse_args()
    
    if args.version:
        try:
            from agent_os import __version__
            print(f"agentos {__version__}")
        except:
            print("agentos (version unknown)")
        return 0
    
    if args.command == "init":
        return cmd_init(args)
    elif args.command == "secure":
        return cmd_secure(args)
    elif args.command == "audit":
        return cmd_audit(args)
    elif args.command == "status":
        return cmd_status(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
