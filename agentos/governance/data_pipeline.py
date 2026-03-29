"""
Data Pipeline — Multi-stage data processing pipeline.

Provides a composable pipeline of named stages for transforming
raw game data into training-ready spans.

Location: agentos/governance/data_pipeline.py

Reference (拿来主义):
  - leagueoflegends-optimizer: data processing pipeline (article5.md)
  - agentlightning/verl/reward_shaping.py: normalization stages
  - DI-star: replay_decoder.py → training data pipeline
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.data_pipeline.v1"


class DataPipeline:
    """Composable multi-stage data processing pipeline.

    Stages are named functions applied sequentially:
    raw_data → stage_1 → stage_2 → ... → processed_data.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._stages: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_stage(self, name: str, fn: Callable[[list], list]) -> None:
        """Add a processing stage.

        Args:
            name: Stage identifier.
            fn: Callable that transforms a list of records.
        """
        self._stages.append({"name": name, "fn": fn})

    def stage_count(self) -> int:
        """Number of registered stages."""
        return len(self._stages)

    def run(self, data: list[Any]) -> list[Any]:
        """Run the pipeline on input data.

        Applies all stages sequentially.

        Args:
            data: Input data list.

        Returns:
            Processed data list.
        """
        result = data
        for stage in self._stages:
            result = stage["fn"](result)

        self._fire_evolution("pipeline_run", {
            "input_count": len(data),
            "output_count": len(result),
            "stages": len(self._stages),
        })
        return result

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
