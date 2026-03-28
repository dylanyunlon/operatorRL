"""
History Evolution Bridge — Connect history data to the self-evolution training loop.

Bridges multiple historical data sources to the AgentLightning training
pipeline, with generation tagging and batch export.

Location: integrations/lol-history/src/lol_history/history_evolution_bridge.py
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.history_evolution_bridge.v1"


class HistoryEvolutionBridge:
    """Bridge historical data sources to evolution training loop.

    Registers data source callables, collects and merges training data,
    tags with generation metadata, and exports in AgentLightning format.
    """

    def __init__(self, generation: int = 0) -> None:
        self.generation = generation
        self.data_sources: dict[str, Callable[[], list[dict]]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_data_source(
        self, name: str, source_fn: Callable[[], list[dict]]
    ) -> None:
        """Register a data source callable.

        Args:
            name: Source identifier.
            source_fn: Callable returning list of training data dicts.
        """
        self.data_sources[name] = source_fn

    def collect_training_data(self) -> list[dict[str, Any]]:
        """Collect training data from all registered sources.

        Returns:
            Merged list of training data dicts.
        """
        all_data: list[dict[str, Any]] = []
        for name, source_fn in self.data_sources.items():
            try:
                data = source_fn()
                all_data.extend(data)
            except Exception as exc:
                logger.warning("Data source %s failed: %s", name, exc)
        return all_data

    def format_for_agentlightning(
        self, spans: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Format spans for AgentLightning training.

        Tags each span with generation metadata.

        Args:
            spans: List of training span dicts.

        Returns:
            Formatted list with generation tags.
        """
        formatted: list[dict[str, Any]] = []
        for span in spans:
            entry = {**span, "generation": self.generation}
            formatted.append(entry)
        return formatted

    def export_training_batch(
        self, batch_size: int = 32
    ) -> list[dict[str, Any]]:
        """Export a training batch.

        Collects data, formats it, and returns up to batch_size items.

        Args:
            batch_size: Maximum number of items.

        Returns:
            List of formatted training dicts.
        """
        data = self.collect_training_data()
        formatted = self.format_for_agentlightning(data)
        return formatted[:batch_size]

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "history_evolution_bridge",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
