"""
Mainboard — System entry point: wires all components and runs the loop.
========================================================================

This is the ``main()`` of lolbot-HyperAI.  It instantiates all modules,
registers them with the CyberScheduler in dependency order, and starts
the while-true event loop that drives the entire system.

Architecture position:
    launch/mainboard.py   ← YOU ARE HERE
    ├─ Instantiates: CanbusComponent, PerceptionComponent,
    │                PredictionComponent, PlanningComponent,
    │                VoiceNarratorComponent
    ├─ Registers with: CyberScheduler (dependency-ordered)
    └─ Runs: scheduler.wait() — blocks until SIGINT/SIGTERM

Apollo reference:
    cyber/mainboard/mainboard.cc  — LoadModule, Start, Wait
    modules/planning/dag/planning.dag — DAG config

Usage:
    python -m launch.mainboard
    python -m launch.mainboard --no-voice
    python -m launch.mainboard --fiddler --fiddler-url http://localhost:8866

Component dependency graph:
    canbus → perception → prediction → planning → voice_narrator
    (each component reads the previous component's channel output)

The system runs until SIGINT (Ctrl+C) or SIGTERM.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# ── Ensure lolbot-HyperAI root is on the import path ────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cyber.logger.cyber_logger import LogConfig, configure, get_logger
from cyber.scheduler.scheduler import CyberScheduler
from modules.canbus.canbus_component import CanbusComponent, CanbusConfig
from modules.perception.perception_component import PerceptionComponent
from modules.prediction.prediction_component import PredictionComponent
from modules.planning.planning_component import PlanningComponent
from modules.control.voice_output.voice_narrator import VoiceNarratorComponent

logger = get_logger("mainboard")


# ─── Banner ──────────────────────────────────────────────────────────────────

_BANNER = r"""
 ╔══════════════════════════════════════════════════════════════════╗
 ║                 lolbot-HyperAI  v0.1.0                         ║
 ║            Apollo-style Real-Time LoL Assistant                 ║
 ║                                                                 ║
 ║  Pipeline:  canbus → perception → prediction → planning → voice ║
 ║  Cycle:     10Hz      10Hz        2Hz          1Hz        1Hz   ║
 ╚══════════════════════════════════════════════════════════════════╝
"""


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="lolbot-HyperAI: Apollo-style real-time LoL assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The system polls the LoL Live Client Data API at 10Hz,\n"
            "processes game state through perception/prediction/planning,\n"
            "and outputs strategy advice via voice narration.\n\n"
            "Press Ctrl+C to stop."
        ),
    )
    parser.add_argument(
        "--lcu-url",
        default="https://127.0.0.1:2999",
        help="LCU Live Client Data API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--fiddler",
        action="store_true",
        help="Enable Fiddler MCP bridge for network capture",
    )
    parser.add_argument(
        "--fiddler-url",
        default="http://127.0.0.1:8866",
        help="Fiddler MCP server URL (default: %(default)s)",
    )
    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="Disable TTS voice narration",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Log output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: %(default)s)",
    )
    parser.add_argument(
        "--canbus-interval",
        type=float,
        default=100.0,
        help="Canbus polling interval in ms (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Initialize but don't start the loop (for testing)",
    )
    return parser.parse_args()


def setup_logging(args: argparse.Namespace) -> None:
    """Configure the global logging system."""
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    configure(LogConfig(
        log_dir=log_dir,
        level=log_level,
        console_output=True,
        json_file_output=True,
        use_color=True,
        collect=True,
    ))


def build_components(args: argparse.Namespace) -> dict:
    """Instantiate all components based on configuration.

    Returns:
        Dict mapping component name to instance.
    """
    components = {}

    # ── 1. Canbus (data acquisition) ─────────────────────────────────
    canbus_config = CanbusConfig(
        lcu_base_url=args.lcu_url,
        fiddler_enabled=args.fiddler,
        fiddler_mcp_url=args.fiddler_url,
        poll_interval_ms=args.canbus_interval,
    )
    components["canbus"] = CanbusComponent(canbus_config)

    # ── 2. Perception (state assembly) ───────────────────────────────
    components["perception"] = PerceptionComponent()

    # ── 3. Prediction (win probability) ──────────────────────────────
    components["prediction"] = PredictionComponent()

    # ── 4. Planning (strategy generation) ────────────────────────────
    components["planning"] = PlanningComponent()

    # ── 5. Voice narrator (TTS output) ───────────────────────────────
    components["voice_narrator"] = VoiceNarratorComponent(
        tts_enabled=not args.no_voice,
    )

    return components


def register_components(
    scheduler: CyberScheduler,
    components: dict,
) -> None:
    """Register all components with the scheduler in dependency order.

    Dependency graph:
        canbus      → (no deps)
        perception  → canbus
        prediction  → perception
        planning    → prediction
        voice       → planning
    """
    scheduler.register(
        components["canbus"],
        deps=[],
        priority=0,
    )
    scheduler.register(
        components["perception"],
        deps=["canbus"],
        priority=1,
    )
    scheduler.register(
        components["prediction"],
        deps=["perception"],
        priority=2,
    )
    scheduler.register(
        components["planning"],
        deps=["prediction"],
        priority=3,
    )
    scheduler.register(
        components["voice_narrator"],
        deps=["planning"],
        priority=4,
    )


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 = success, 1 = error).
    """
    args = parse_args()

    # ── Setup logging ────────────────────────────────────────────────
    setup_logging(args)

    print(_BANNER)
    logger.info("Starting lolbot-HyperAI...")
    logger.info("Config: lcu_url=%s fiddler=%s voice=%s",
                args.lcu_url, args.fiddler, not args.no_voice)

    # ── Build components ─────────────────────────────────────────────
    components = build_components(args)
    logger.info("Built %d components", len(components))

    # ── Create scheduler and register components ─────────────────────
    scheduler = CyberScheduler()
    register_components(scheduler, components)

    logger.info("Scheduler summary: %s", scheduler.summary())

    # ── Dry-run mode: init only ──────────────────────────────────────
    if args.dry_run:
        logger.info("Dry-run mode: initializing components without starting loop")
        for name in scheduler.component_names:
            comp = scheduler.get_component(name)
            if comp:
                if comp.initialize():
                    logger.info("  %s: Init OK", name)
                else:
                    logger.error("  %s: Init FAILED", name)
                    return 1
        logger.info("Dry-run complete. All components initialized successfully.")
        return 0

    # ── Start all components (the while-true loop begins) ────────────
    logger.info("Starting all components...")
    if not scheduler.start_all():
        logger.error("Failed to start components. Exiting.")
        return 1

    logger.info(
        "System running. Pipeline: canbus(10Hz) → perception(10Hz) → "
        "prediction(2Hz) → planning(1Hz) → voice(1Hz)"
    )
    logger.info("Press Ctrl+C to stop.")

    # ── Block until shutdown signal ──────────────────────────────────
    try:
        scheduler.wait()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
        scheduler.stop_all()

    logger.info("lolbot-HyperAI stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
