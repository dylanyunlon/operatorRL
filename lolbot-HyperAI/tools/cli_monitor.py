"""
CLIMonitor — Terminal real-time pipeline monitoring tool.
==========================================================
lolbot-HyperAI · Tools Layer

Displays real-time pipeline status in the terminal using ANSI escape
codes.  Shows win probability, strategy advice, component health,
and recent events in a compact dashboard format.

Architecture position:
    tools/cli_monitor.py   ← YOU ARE HERE
    ├─ Reads: Dreamview API (HTTP polling)
    ├─ Reads: Direct component stats (when in-process)
    └─ Used by: operators for monitoring during development

Apollo reference:
    cyber/tools/cyber_monitor/ — CyberRT monitoring tool
    modules/dreamview/backend/ — backend status API

Design notes:
    - ANSI escape codes for colors and cursor positioning
    - Polling mode: HTTP GET to Dreamview API
    - In-process mode: direct access to component stats()
    - Auto-size to terminal dimensions
    - Graceful degradation if terminal doesn't support ANSI
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ─── ANSI Escape Codes ──────────────────────────────────────────────────────

class _ANSI:
    """ANSI escape code helpers."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    # Colors
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    # Background
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    # Cursor
    CLEAR_SCREEN = "\033[2J"
    HOME = "\033[H"
    CLEAR_LINE = "\033[K"

    @staticmethod
    def move(row: int, col: int) -> str:
        return f"\033[{row};{col}H"

    @staticmethod
    def color_value(value: float, low: float, high: float) -> str:
        """Color a value: red < low, yellow mid, green > high."""
        if value >= high:
            return f"{_ANSI.GREEN}{value:.1f}{_ANSI.RESET}"
        elif value >= low:
            return f"{_ANSI.YELLOW}{value:.1f}{_ANSI.RESET}"
        else:
            return f"{_ANSI.RED}{value:.1f}{_ANSI.RESET}"


# ─── Data Structures ────────────────────────────────────────────────────────

@dataclass
class MonitorState:
    """Current monitor display state."""
    connected: bool = False
    state: str = "unknown"
    tick_count: int = 0
    error_count: int = 0
    uptime_s: float = 0.0
    win_probability: float = 0.5
    strategy_text: str = "--"
    macro_action: str = "IDLE"
    macro_rationale: str = "--"
    game_time: float = 0.0
    components: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    events: List[str] = field(default_factory=list)
    last_update: float = field(default_factory=time.monotonic)


# ─── Dashboard Renderer ─────────────────────────────────────────────────────

class DashboardRenderer:
    """Renders the monitoring dashboard to terminal."""

    def __init__(self, width: int = 80) -> None:
        self._width = width

    def render(self, state: MonitorState) -> str:
        """Render the full dashboard as a string."""
        lines = []
        A = _ANSI

        # Header
        lines.append(f"{A.CLEAR_SCREEN}{A.HOME}")
        lines.append(self._header_line(state))
        lines.append(self._separator())

        # Win Probability
        lines.append(self._win_prob_line(state.win_probability))

        # Strategy
        lines.append(f"  {A.BOLD}Strategy:{A.RESET} {state.strategy_text[:60]}")
        lines.append(
            f"  {A.BOLD}Macro:{A.RESET} {A.CYAN}{state.macro_action}{A.RESET}"
            f" — {state.macro_rationale[:50]}"
        )
        lines.append(self._separator())

        # Component status
        lines.append(f"  {A.BOLD}Components:{A.RESET}")
        for name, info in state.components.items():
            status = self._component_status(info)
            lines.append(f"    {name:20s} {status}")
        lines.append(self._separator())

        # Stats
        game_min = int(state.game_time) // 60
        game_sec = int(state.game_time) % 60
        lines.append(
            f"  {A.DIM}Game Time: {game_min}:{game_sec:02d}  |  "
            f"Ticks: {state.tick_count}  |  "
            f"Errors: {state.error_count}  |  "
            f"Uptime: {state.uptime_s:.0f}s{A.RESET}"
        )
        lines.append(self._separator())

        # Events
        lines.append(f"  {A.BOLD}Recent Events:{A.RESET}")
        for event_text in state.events[-5:]:
            lines.append(f"    {A.DIM}{event_text}{A.RESET}")

        # Footer
        lines.append("")
        lines.append(f"  {A.DIM}Press Ctrl+C to exit  |  Refresh: 500ms{A.RESET}")

        return "\n".join(lines)

    def _header_line(self, state: MonitorState) -> str:
        A = _ANSI
        conn_color = A.GREEN if state.connected else A.RED
        conn_text = "CONNECTED" if state.connected else "DISCONNECTED"
        return (
            f"  {A.BOLD}{A.CYAN}lolbot-HyperAI Monitor{A.RESET}"
            f"  [{conn_color}{conn_text}{A.RESET}]"
            f"  State: {A.YELLOW}{state.state}{A.RESET}"
        )

    def _separator(self) -> str:
        return f"  {_ANSI.DIM}{'─' * (self._width - 4)}{_ANSI.RESET}"

    def _win_prob_line(self, prob: float) -> str:
        A = _ANSI
        pct = int(prob * 100)
        bar_width = 40
        filled = int(bar_width * prob)
        empty = bar_width - filled

        if prob >= 0.55:
            color = A.GREEN
        elif prob >= 0.45:
            color = A.YELLOW
        else:
            color = A.RED

        bar = f"{color}{'█' * filled}{A.DIM}{'░' * empty}{A.RESET}"
        return f"  {A.BOLD}Win:{A.RESET} [{bar}] {color}{pct}%{A.RESET}"

    def _component_status(self, info: Dict[str, Any]) -> str:
        A = _ANSI
        errors = info.get("error_count", info.get("total_failures", 0))
        if errors > 5:
            return f"{A.BG_RED}{A.WHITE} ERROR {A.RESET} ({errors} errors)"
        elif errors > 0:
            return f"{A.YELLOW} WARN {A.RESET} ({errors} errors)"
        else:
            return f"{A.GREEN} OK {A.RESET}"


# ─── Data Fetcher ────────────────────────────────────────────────────────────

class HTTPDataFetcher:
    """Fetches monitoring data from Dreamview HTTP API."""

    def __init__(self, base_url: str = "http://localhost:8080/api") -> None:
        self._base_url = base_url
        self._timeout = 2.0

    def fetch(self) -> Optional[Dict[str, Any]]:
        """Fetch current state from the API."""
        try:
            req = urllib.request.Request(
                f"{self._base_url}/state",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None


class DirectDataFetcher:
    """Fetches monitoring data directly from in-process components."""

    def __init__(self, main_loop: Any = None) -> None:
        self._main_loop = main_loop

    def fetch(self) -> Optional[Dict[str, Any]]:
        if self._main_loop is None:
            return None
        try:
            return self._main_loop.stats()
        except Exception:
            return None


# ─── CLI Monitor ─────────────────────────────────────────────────────────────

class CLIMonitor:
    """Terminal-based real-time monitoring dashboard.

    Usage::

        # HTTP polling mode:
        monitor = CLIMonitor(api_url="http://localhost:8080/api")
        monitor.run()

        # In-process mode:
        monitor = CLIMonitor(main_loop=loop_instance)
        monitor.run()
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        main_loop: Any = None,
        refresh_ms: int = 500,
    ) -> None:
        if api_url:
            self._fetcher = HTTPDataFetcher(api_url)
        else:
            self._fetcher = DirectDataFetcher(main_loop)

        self._refresh_s = refresh_ms / 1000.0
        self._state = MonitorState()
        self._renderer = DashboardRenderer(width=self._get_terminal_width())
        self._running = False

    def run(self) -> None:
        """Run the monitoring loop until Ctrl+C."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)

        print(_ANSI.CLEAR_SCREEN, end="", flush=True)

        while self._running:
            self._update()
            output = self._renderer.render(self._state)
            print(output, end="", flush=True)
            time.sleep(self._refresh_s)

        # Cleanup
        print(f"\n{_ANSI.RESET}Monitor stopped.\n")

    def _update(self) -> None:
        """Fetch and update state."""
        data = self._fetcher.fetch()

        if data is None:
            self._state.connected = False
            return

        self._state.connected = True
        self._state.state = data.get("state", "unknown")
        self._state.tick_count = data.get("tick_count", 0)
        self._state.error_count = data.get("error_count", 0)
        self._state.uptime_s = data.get("uptime_sec", 0.0)
        self._state.win_probability = data.get("win_probability", 0.5)
        self._state.game_time = data.get("game_time", 0.0)

        if "strategy" in data:
            self._state.strategy_text = data["strategy"].get("text", "--")
        if "macro" in data:
            self._state.macro_action = data["macro"].get("action", "IDLE")
            self._state.macro_rationale = data["macro"].get("rationale", "--")
        if "components" in data:
            self._state.components = data["components"]

        self._state.last_update = time.monotonic()

    def _handle_signal(self, sig, frame) -> None:
        self._running = False

    @staticmethod
    def _get_terminal_width() -> int:
        try:
            return os.get_terminal_size().columns
        except (OSError, ValueError):
            return 80


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point for the monitor tool."""
    import argparse
    parser = argparse.ArgumentParser(description="lolbot-HyperAI CLI Monitor")
    parser.add_argument(
        "--url", default="http://localhost:8080/api",
        help="Dreamview API URL",
    )
    parser.add_argument(
        "--refresh", type=int, default=500,
        help="Refresh interval in ms",
    )
    args = parser.parse_args()

    monitor = CLIMonitor(api_url=args.url, refresh_ms=args.refresh)
    monitor.run()


if __name__ == "__main__":
    main()
