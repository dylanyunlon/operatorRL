"""
Cross-Game Dashboard — Unified monitoring view.

Provides a panel-based dashboard for monitoring metrics across
all games, with global snapshot and report export.

Location: agentos/governance/cross_game_dashboard.py

Reference (拿来主义):
  - agentlightning/algorithm/self_play_scheduler.py: leaderboard/ranking pattern
  - operatorRL: dashboard design from plan.md
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.cross_game_dashboard.v1"


class CrossGameDashboard:
    """Unified monitoring dashboard for all game agents.

    Manages per-game panels with named metrics, supports
    global snapshot and report export.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        # game → {metric_name → value}
        self._panels: dict[str, dict[str, Any]] = {}
        self._panel_metrics: dict[str, list[str]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_panel(self, game: str, metrics: list[str]) -> None:
        """Register a game panel with its metrics.

        Args:
            game: Game identifier.
            metrics: List of metric names to track.
        """
        self._panels[game] = {m: 0.0 for m in metrics}
        self._panel_metrics[game] = metrics

    def remove_panel(self, game: str) -> None:
        """Remove a game panel."""
        self._panels.pop(game, None)
        self._panel_metrics.pop(game, None)

    def panel_count(self) -> int:
        """Number of registered panels."""
        return len(self._panels)

    def update_metric(self, game: str, metric: str, value: float) -> None:
        """Update a metric value on a game panel.

        Args:
            game: Game identifier.
            metric: Metric name.
            value: New metric value.
        """
        if game in self._panels:
            self._panels[game][metric] = value
            self._fire_evolution("metric_updated", {
                "game": game, "metric": metric, "value": value,
            })

    def get_snapshot(self, game: str) -> dict[str, Any]:
        """Get current metric snapshot for a game.

        Args:
            game: Game identifier.

        Returns:
            Dict of metric → value.
        """
        return dict(self._panels.get(game, {}))

    def get_global_snapshot(self) -> dict[str, dict[str, Any]]:
        """Get snapshots for all games.

        Returns:
            Dict of game → {metric → value}.
        """
        return {g: dict(m) for g, m in self._panels.items()}

    def export_report(self) -> dict[str, Any]:
        """Export a full dashboard report.

        Returns:
            Report dict with all panels and metadata.
        """
        return {
            "timestamp": time.time(),
            "panels": self.get_global_snapshot(),
            "panel_count": self.panel_count(),
        }

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
