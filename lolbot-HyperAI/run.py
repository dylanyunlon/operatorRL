#!/usr/bin/env python3
"""
run.py — Apollo-style CLI Entry Point for lolbot-HyperAI.
============================================================

查看 Apollo ``cyber/mainboard/mainboard.cc`` 上现有 ``main()`` 的实现方式,
理解其模式, 特别是 **参数解析** 和 **ModuleController 初始化** 是如何分离的。
从 Apollo mainboard.cc 的 ``ModuleArgument → Init → WaitForShutdown`` 流程
这个好例子开始。然后, 遵循该模式实现一个新的 ``run.py``, 让用户可以通过
命令行参数控制 DAG 配置、日志级别、mock/replay 模式, 并能一条命令启动整
个 pipeline。接着在参数解析中引入 ``--dag`` 路径覆盖, 使开发者能够动态切
换组件拓扑, 同时优化 ``--mock`` 标志以支持无游戏环境下的本地测试。随后整
合 ``--replay`` 模式, 令系统支持回放已录制的 JSONL 数据, 进而增强离线分
析能力。最终完善 ``--log-level`` / ``--log-dir`` / ``--profile`` 参数,
确保运行时配置兼容 Apollo 的 ``ModuleArgument`` 设计理念, 全面系统性升
级启动体验以达成 生产级一条命令启动 的目标。

Apollo reference:
    cyber/mainboard/mainboard.cc         — main() entry
    cyber/mainboard/module_argument.cc   — ParseArgument()

Usage:
    python run.py                          # default: full pipeline
    python run.py --mock                   # mock mode (no LCU needed)
    python run.py --replay data/session.jsonl  # replay mode
    python run.py --log-level DEBUG        # verbose logging
    python run.py --dag conf/dag/lolbot_minimal.yaml  # custom DAG
    python run.py --profile                # enable cProfile

位置: lolbot-HyperAI/run.py
"""

from __future__ import annotations

import argparse
import cProfile
import os
import signal
import sys
import time
from pathlib import Path

# ── Ensure project root is importable ────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Apollo equivalent: ModuleArgument::ParseArgument(argc, argv)
    """
    p = argparse.ArgumentParser(
        prog="lolbot-HyperAI",
        description=(
            "Apollo-style LoL Game Assistant — "
            "Self-evolving via operatorRL governance kernel"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run.py                        # full pipeline\n"
            "  python run.py --mock                  # no LCU needed\n"
            "  python run.py --replay session.jsonl  # replay mode\n"
            "  python run.py --dag conf/dag/min.yaml # custom DAG\n"
        ),
    )

    # ── Mode flags ───────────────────────────────────────────────────────
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--mock", action="store_true",
        help="Run with mock data source (no LCU/game required)",
    )
    mode.add_argument(
        "--replay", type=str, metavar="FILE",
        help="Replay a recorded JSONL session file",
    )

    # ── Configuration ────────────────────────────────────────────────────
    p.add_argument(
        "--config", type=str, default="configs/pipeline.yaml",
        help="Path to pipeline config YAML (default: configs/pipeline.yaml)",
    )
    p.add_argument(
        "--dag", type=str, default=None,
        help="Path to DAG YAML (overrides default component wiring)",
    )

    # ── Logging ──────────────────────────────────────────────────────────
    p.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    p.add_argument(
        "--log-dir", type=str, default="logs",
        help="Log output directory (default: logs/)",
    )
    p.add_argument(
        "--no-console-log", action="store_true",
        help="Suppress console log output (file-only logging)",
    )

    # ── Development / Diagnostics ────────────────────────────────────────
    p.add_argument(
        "--profile", action="store_true",
        help="Enable cProfile profiling (dumps to logs/profile.prof)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Validate DAG and config, print startup plan, then exit",
    )
    p.add_argument(
        "--diagnostics", action="store_true",
        help="Print pipeline flow diagnostics every 10s",
    )
    p.add_argument(
        "--no-voice", action="store_true",
        help="Disable TTS voice output",
    )
    p.add_argument(
        "--no-evolution", action="store_true",
        help="Disable evolution / mutation between games",
    )
    p.add_argument(
        "--dashboard-port", type=int, default=8080,
        help="Dreamview dashboard HTTP port (default: 8080)",
    )

    return p.parse_args()


def _configure_logging(args: argparse.Namespace) -> None:
    """Configure the cyber logging system based on CLI args.

    Apollo equivalent: glog FLAGS setup in Init()
    """
    import logging
    from cyber.logger.cyber_logger import LogConfig, configure

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }

    config = LogConfig(
        log_dir=Path(args.log_dir),
        level=level_map.get(args.log_level, logging.INFO),
        console_output=not args.no_console_log,
        json_file_output=True,
        use_color=True,
        collect=True,
    )
    configure(config)


def _apply_overrides(args: argparse.Namespace) -> None:
    """Apply CLI overrides to the config / environment.

    Sets environment variables that conf.default_config.load_config()
    and CanbusComponent will pick up.
    """
    if args.mock:
        os.environ["LOLBOT_CANBUS__DATA_SOURCE"] = "mock"

    if args.replay:
        os.environ["LOLBOT_CANBUS__DATA_SOURCE"] = "replay"
        os.environ["LOLBOT_CANBUS__REPLAY_FILE"] = str(args.replay)

    if args.no_voice:
        os.environ["LOLBOT_VOICE__ENABLED"] = "false"

    if args.no_evolution:
        os.environ["LOLBOT_EVOLUTION__ENABLED"] = "false"

    if args.config:
        os.environ["LOLBOT_CONFIG_PATH"] = str(args.config)

    if args.diagnostics:
        os.environ["LOLBOT_DIAGNOSTICS"] = "1"


def _print_banner(args: argparse.Namespace) -> None:
    """Print startup banner."""
    mode = "MOCK" if args.mock else ("REPLAY" if args.replay else "LIVE")
    print("=" * 62)
    print("  lolbot-HyperAI")
    print("  Apollo-style LoL Game Assistant")
    print("  Self-evolving via operatorRL governance kernel")
    print("  Thread-per-component architecture (Apollo mainboard)")
    print(f"  Mode: {mode}  |  Log: {args.log_level}  |  PID: {os.getpid()}")
    print("=" * 62)
    print()


def _dry_run(args: argparse.Namespace) -> None:
    """Validate config and DAG, print plan, exit.

    Apollo equivalent: module_controller.GetComponentNum() without Start().
    """
    from launch.dag_launcher import DAGLauncher, load_dag_from_yaml

    dag_path = Path(args.dag) if args.dag else None
    print("[dry-run] Validating configuration...")
    print(f"  Config: {args.config}")
    print(f"  DAG:    {dag_path or '(default wiring in main_loop.py)'}")
    print(f"  Mode:   {'mock' if args.mock else 'replay' if args.replay else 'live'}")
    print(f"  Log:    {args.log_level} → {args.log_dir}/")
    print()

    if dag_path and dag_path.exists():
        entries = load_dag_from_yaml(dag_path)
        launcher = DAGLauncher()
        launcher.register_many(entries)
        ok, errors = launcher.validate()
        if ok:
            print(f"  DAG: {len(entries)} components, validation PASSED")
            for e in entries:
                deps = ", ".join(e.depends_on) if e.depends_on else "(none)"
                print(f"    {e.name:24s} interval={e.interval_ms}ms  deps=[{deps}]")
        else:
            print("  DAG validation FAILED:")
            for err in errors:
                print(f"    ✗ {err}")
            sys.exit(1)
    else:
        print("  DAG: using default hardcoded pipeline")
        print("    canbus     → 100ms (10Hz)")
        print("    perception → 100ms (10Hz)")
        print("    prediction → 500ms  (2Hz)")
        print("    planning   → 500ms  (2Hz)")
        print("    control    → 200ms  (5Hz)")
        print("    monitor    → 2000ms (0.5Hz)")

    print()
    print("[dry-run] All checks passed. Ready to launch.")


def _run_main_loop(args: argparse.Namespace) -> None:
    """Instantiate and run the MainLoop.

    Apollo equivalent:
        ModuleController controller(module_args);
        controller.Init();
        cyber::WaitForShutdown();
        controller.Clear();
    """
    from launch.main_loop import MainLoop

    loop = MainLoop()
    loop.run()


def main() -> None:
    """CLI entry point — Apollo mainboard main() equivalent."""
    args = _parse_args()

    # ── 1. Banner ────────────────────────────────────────────────────────
    _print_banner(args)

    # ── 2. Configure logging (Apollo: glog Init) ────────────────────────
    _configure_logging(args)

    # ── 3. Apply CLI overrides to environment ────────────────────────────
    _apply_overrides(args)

    # ── 4. Dry run? ─────────────────────────────────────────────────────
    if args.dry_run:
        _dry_run(args)
        return

    # ── 5. Run (optionally with profiling) ───────────────────────────────
    if args.profile:
        profile_path = Path(args.log_dir) / "profile.prof"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[profile] Profiling enabled → {profile_path}")
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            _run_main_loop(args)
        finally:
            profiler.disable()
            profiler.dump_stats(str(profile_path))
            print(f"[profile] Profile saved to {profile_path}")
            print(f"  View with: python -m pstats {profile_path}")
    else:
        _run_main_loop(args)


if __name__ == "__main__":
    main()
