"""
Telemetry Collector — Cross-game performance metrics aggregation.

Collects, stores, and queries performance metrics across all games
managed by AgentOS.

Location: agentos/governance/telemetry_collector.py

Reference (拿来主义):
  - agentlightning/store/experience_store.py: deque storage pattern
  - agentlightning/verl/reward_shaping.py: metric normalization
  - operatorRL: telemetry design from plan.md
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.telemetry_collector.v1"


class TelemetryCollector:
    """Cross-game telemetry metric collector.

    Stores timestamped metric values per game, supports latest/average
    queries, and fires evolution callbacks on new data.

    Attributes:
        max_history: Maximum metric entries per game-metric pair.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, max_history: int = 1000) -> None:
        self.max_history = max_history
        self._metrics: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=max_history))
        )
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record(self, game: str, metric: str, value: float) -> None:
        """Record a metric value.

        Args:
            game: Game identifier.
            metric: Metric name.
            value: Metric value.
        """
        self._metrics[game][metric].append({
            "value": value,
            "timestamp": time.time(),
        })
        self._fire_evolution("metric_recorded", {
            "game": game, "metric": metric, "value": value,
        })

    def get_metrics(self, game: str) -> dict[str, list[dict]]:
        """Get all metrics for a game.

        Args:
            game: Game identifier.

        Returns:
            Dict mapping metric name → list of {value, timestamp}.
            Returns empty dict if game has no metrics.
        """
        if game not in self._metrics:
            return {}
        return {k: list(v) for k, v in self._metrics[game].items()}

    def get_latest(self, game: str, metric: str) -> Optional[float]:
        """Get the latest value for a specific metric.

        Args:
            game: Game identifier.
            metric: Metric name.

        Returns:
            Latest value, or None if no data.
        """
        if game not in self._metrics or metric not in self._metrics[game]:
            return None
        entries = self._metrics[game][metric]
        if not entries:
            return None
        return entries[-1]["value"]

    def get_average(self, game: str, metric: str) -> float:
        """Get average value for a specific metric.

        Args:
            game: Game identifier.
            metric: Metric name.

        Returns:
            Average value, or 0.0 if no data.
        """
        if game not in self._metrics or metric not in self._metrics[game]:
            return 0.0
        entries = self._metrics[game][metric]
        if not entries:
            return 0.0
        return sum(e["value"] for e in entries) / len(entries)

    def list_games_with_metrics(self) -> list[str]:
        """List all games that have recorded metrics."""
        return list(self._metrics.keys())

    def clear(self, game: str) -> None:
        """Clear all metrics for a game.

        Args:
            game: Game identifier.
        """
        if game in self._metrics:
            del self._metrics[game]

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
