#!/usr/bin/env python3
"""
run_with_logs.py — Launch lolbot-HyperAI with structured log collection
==========================================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Entry point script that:
  1. Configures structured logging (JSON format for log analysis)
  2. Detects and recovers from dirty shutdowns
  3. Starts the ProcessManager with all available modules
  4. Collects logs for the Evolution layer to analyze
  5. Provides a minimal health HTTP endpoint (:8080/health)

Usage:
    python scripts/run_with_logs.py
    python scripts/run_with_logs.py --log-level DEBUG
    python scripts/run_with_logs.py --tick-ms 50 --no-voice

This script is the recommended way to run lolbot-HyperAI during
development and testing. For production, use the Docker container.
"""

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any, Dict, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.process_manager import ProcessManager, ComponentPriority
from runtime.health_monitor import HealthMonitor, DependencyType
from runtime.metrics_collector import MetricsCollector
from runtime.graceful_shutdown import (
    GracefulShutdown, ShutdownPhase, RecoveryDetector,
)
from runtime.error_recovery import ComponentHealer
from integration.event_dispatcher import EventDispatcher
from integration.module_registry import ModuleRegistry, ModuleCategory
from integration.pipeline_builder import PipelineBuilder, DEFAULT_LOL_PIPELINE
from integration.agent_os_bridge import AgentOSBridge
from integration.plugin_loader import PluginLoader


# ---------------------------------------------------------------------------
# Structured JSON log formatter
# ---------------------------------------------------------------------------

class JsonLogFormatter(logging.Formatter):
    """Outputs log records as single-line JSON for easy parsing by Evolution."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Minimal health HTTP server
# ---------------------------------------------------------------------------

_health_data: Dict[str, Any] = {"overall": "starting"}


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for health checks."""

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_health_data).encode())
        elif self.path == "/metrics":
            # Prometheus text format
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                _health_data.get("prometheus_text", "").encode()
            )
        elif self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_health_data, indent=2).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress HTTP logs


def start_health_server(port: int = 8080) -> HTTPServer:
    """Start health endpoint in background thread."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# Configure logging
# ---------------------------------------------------------------------------

def setup_logging(
    log_level: str = "INFO",
    log_dir: Optional[str] = None,
    json_format: bool = True,
) -> None:
    """Configure structured logging with file rotation."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    if json_format:
        console.setFormatter(JsonLogFormatter())
    else:
        console.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
    root_logger.addHandler(console)

    # File handler (if log_dir specified)
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / "lolbot.jsonl",
            maxBytes=50 * 1024 * 1024,  # 50 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonLogFormatter())
        root_logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Module discovery and registration
# ---------------------------------------------------------------------------

def discover_and_register(
    registry: ModuleRegistry,
    health: HealthMonitor,
    metrics: MetricsCollector,
    dispatcher: EventDispatcher,
    bridge: AgentOSBridge,
) -> None:
    """
    Register all available runtime/integration modules.
    Claude #1 and #2 modules will be discovered via PluginLoader.
    """
    log = logging.getLogger("lolbot.startup")

    # Runtime modules (always available — our code)
    registry.register(
        name=health.name,
        category=ModuleCategory.RUNTIME,
        instance=health,
        version="1.0.0",
        interval_ms=1000,
        priority=ComponentPriority.RUNTIME,
    )
    registry.register(
        name=metrics.name,
        category=ModuleCategory.RUNTIME,
        instance=metrics,
        version="1.0.0",
        interval_ms=5000,
        priority=ComponentPriority.RUNTIME,
    )
    registry.register(
        name=dispatcher.name,
        category=ModuleCategory.RUNTIME,
        instance=dispatcher,
        version="1.0.0",
        interval_ms=10,  # Dispatch events every tick
        priority=ComponentPriority.RUNTIME,
    )
    registry.register(
        name=bridge.name,
        category=ModuleCategory.INTEGRATION,
        instance=bridge,
        version="1.0.0",
        interval_ms=5000,
        priority=ComponentPriority.RUNTIME,
    )

    log.info(
        "Registered %d core modules", registry.module_count
    )

    # Try to discover Claude #1 and #2 modules
    loader = PluginLoader(
        plugin_dirs=[
            str(PROJECT_ROOT / "cyber"),
            str(PROJECT_ROOT / "core"),
            str(PROJECT_ROOT / "perception"),
            str(PROJECT_ROOT / "planning"),
            str(PROJECT_ROOT / "prediction"),
            str(PROJECT_ROOT / "analysis"),
            str(PROJECT_ROOT / "output"),
            str(PROJECT_ROOT / "evolution"),
        ],
        m_series_base=str(PROJECT_ROOT.parent),
    )
    discovered = loader.discover()
    log.info(
        "Discovered %d additional modules from plugins/M-series",
        len(discovered),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    """Main entry point."""
    global _health_data
    log = logging.getLogger("lolbot.main")

    log.info("=" * 60)
    log.info("lolbot-HyperAI starting")
    log.info("  Tick rate: %d ms (%d Hz)", args.tick_ms, 1000 // args.tick_ms)
    log.info("  Log level: %s", args.log_level)
    log.info("=" * 60)

    # Initialize core components
    checkpoint_dir = args.checkpoint_dir or str(PROJECT_ROOT / "data" / "checkpoints")
    data_dir = args.data_dir or str(PROJECT_ROOT / "data")

    health = HealthMonitor()
    metrics = MetricsCollector()
    dispatcher = EventDispatcher()
    bridge = AgentOSBridge(kernel_url=os.environ.get("LOLBOT_KERNEL_URL"))
    registry = ModuleRegistry()
    shutdown_mgr = GracefulShutdown(checkpoint_dir=checkpoint_dir)
    healer = ComponentHealer()

    # Recovery check
    recovery = RecoveryDetector(checkpoint_dir=checkpoint_dir)
    recovery_result = await recovery.check_and_recover()
    if recovery_result["recovery_needed"]:
        log.warning("Recovery completed: %s", recovery_result)

    # Register modules
    discover_and_register(registry, health, metrics, dispatcher, bridge)

    # Build ProcessManager
    pm = ProcessManager(
        base_tick_ms=args.tick_ms,
        enable_signal_handling=True,
    )

    # Register all modules in ProcessManager
    for desc in registry._modules.values():
        if desc.instance:
            pm.register(
                desc.instance,
                interval_ms=desc.interval_ms,
                priority=desc.priority,
            )

    # Register shutdown tasks
    shutdown_mgr.register(
        ShutdownPhase.FLUSH_DATA,
        "flush_metrics",
        metrics.shutdown,
        timeout_s=2.0,
    )
    shutdown_mgr.register(
        ShutdownPhase.CLOSE_IO,
        "close_bridge",
        bridge.shutdown,
        timeout_s=3.0,
    )
    shutdown_mgr.register(
        ShutdownPhase.CLOSE_IO,
        "close_dispatcher",
        dispatcher.shutdown,
        timeout_s=2.0,
    )

    # Start health server
    health_port = int(os.environ.get("LOLBOT_HEALTH_PORT", "8080"))
    health_server = start_health_server(health_port)
    log.info("Health endpoint: http://0.0.0.0:%d/health", health_port)

    # Update health data periodically
    async def update_health():
        while not pm._shutdown_requested:
            _health_data.update(health.get_report())
            _health_data["process_manager"] = pm.get_status()
            _health_data["prometheus_text"] = metrics.prometheus_text()
            await asyncio.sleep(1.0)

    health_task = asyncio.create_task(update_health())

    # Run the main loop
    log.info("Entering main loop — Ctrl+C to stop")
    try:
        await pm.start()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
    finally:
        health_task.cancel()
        report = await shutdown_mgr.execute()
        log.info("Shutdown complete: %s", report.to_dict())
        health_server.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="lolbot-HyperAI — Self-evolving LoL Game Assistant",
    )
    parser.add_argument(
        "--tick-ms", type=int,
        default=int(os.environ.get("LOLBOT_BASE_TICK_MS", "10")),
        help="Base tick interval in milliseconds (default: 10)",
    )
    parser.add_argument(
        "--log-level", type=str,
        default=os.environ.get("LOLBOT_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--log-dir", type=str,
        default=os.environ.get("LOLBOT_LOG_DIR"),
    )
    parser.add_argument(
        "--checkpoint-dir", type=str,
        default=os.environ.get("LOLBOT_CHECKPOINT_DIR"),
    )
    parser.add_argument(
        "--data-dir", type=str,
        default=os.environ.get("LOLBOT_DATA_DIR"),
    )
    parser.add_argument(
        "--no-json-logs", action="store_true",
        help="Use human-readable log format instead of JSON",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    setup_logging(
        log_level=args.log_level,
        log_dir=args.log_dir,
        json_format=not args.no_json_logs,
    )
    asyncio.run(main(args))
