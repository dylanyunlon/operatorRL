"""
Data Pipeline - ETL pipeline for game data processing.

Processes raw Fiddler captures through a configurable chain of
transformers, producing cleaned game snapshots for analysis and ML.

Pipeline stages:
  Raw HTTP → Parse JSON → Validate Schema → Extract State →
  Compute Features → Enrich (champion DB) → Store/Stream
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Generic, Optional, TypeVar

from lol_fiddler_agent.models.game_snapshot import GameSnapshot
from lol_fiddler_agent.network.live_client_data import LiveGameState, parse_captured_game_state
from lol_fiddler_agent.network.packet_analyzer import AnalyzedPacket, APIEndpointCategory

logger = logging.getLogger(__name__)

T = TypeVar("T")
U = TypeVar("U")


class PipelineStage(ABC, Generic[T, U]):
    """Abstract pipeline stage that transforms T → U."""

    @abstractmethod
    async def process(self, data: T) -> Optional[U]:
        """Process input data, returning None to drop the item."""
        pass

    @property
    def name(self) -> str:
        return self.__class__.__name__


class ParseStage(PipelineStage[AnalyzedPacket, LiveGameState]):
    """Parse raw packet body into LiveGameState."""

    async def process(self, data: AnalyzedPacket) -> Optional[LiveGameState]:
        if data.category != APIEndpointCategory.LIVE_CLIENT_ALL_GAME:
            return None
        if not data.session.response_body:
            return None
        return parse_captured_game_state(data.session.response_body)


class ValidateStage(PipelineStage[LiveGameState, LiveGameState]):
    """Validate game state has minimum required fields."""

    async def process(self, data: LiveGameState) -> Optional[LiveGameState]:
        if not data.game_data:
            logger.debug("Dropped state: no game_data")
            return None
        if not data.active_player:
            logger.debug("Dropped state: no active_player")
            return None
        if len(data.all_players) < 2:
            logger.debug("Dropped state: too few players (%d)", len(data.all_players))
            return None
        return data


class SnapshotStage(PipelineStage[LiveGameState, GameSnapshot]):
    """Convert mutable LiveGameState to immutable GameSnapshot."""

    async def process(self, data: LiveGameState) -> Optional[GameSnapshot]:
        try:
            return GameSnapshot.from_live_state(data)
        except Exception as e:
            logger.warning("Snapshot creation failed: %s", e)
            return None


class DeduplicateStage(PipelineStage[GameSnapshot, GameSnapshot]):
    """Drop duplicate snapshots based on content hash."""

    def __init__(self, max_cache: int = 256) -> None:
        self._seen: dict[str, float] = {}
        self._max_cache = max_cache

    async def process(self, data: GameSnapshot) -> Optional[GameSnapshot]:
        h = data.compute_hash()
        if h in self._seen:
            return None
        self._seen[h] = time.time()
        # Evict old
        if len(self._seen) > self._max_cache:
            oldest = sorted(self._seen, key=self._seen.get)[:self._max_cache // 2]
            for k in oldest:
                del self._seen[k]
        return data


@dataclass
class PipelineMetrics:
    """Metrics for pipeline execution."""
    total_input: int = 0
    total_output: int = 0
    total_dropped: int = 0
    total_errors: int = 0
    stage_times_ms: dict[str, float] = field(default_factory=dict)
    stage_drop_counts: dict[str, int] = field(default_factory=dict)

    @property
    def drop_rate(self) -> float:
        if self.total_input == 0:
            return 0.0
        return self.total_dropped / self.total_input

    @property
    def throughput(self) -> float:
        total_ms = sum(self.stage_times_ms.values())
        if total_ms == 0:
            return 0.0
        return self.total_output / (total_ms / 1000)


class DataPipeline:
    """Configurable data processing pipeline.

    Chains multiple stages together, with metrics collection
    and error handling at each stage.

    Example::

        pipeline = DataPipeline()
        pipeline.add_stage(ParseStage())
        pipeline.add_stage(ValidateStage())
        pipeline.add_stage(SnapshotStage())
        pipeline.add_stage(DeduplicateStage())

        result = await pipeline.run(analyzed_packet)
        if result is not None:
            process_snapshot(result)
    """

    def __init__(self) -> None:
        self._stages: list[PipelineStage] = []
        self._metrics = PipelineMetrics()
        self._output_callbacks: list[Callable] = []

    def add_stage(self, stage: PipelineStage) -> "DataPipeline":
        self._stages.append(stage)
        return self

    def on_output(self, callback: Callable) -> None:
        """Register a callback for pipeline output."""
        self._output_callbacks.append(callback)

    async def run(self, data: Any) -> Optional[Any]:
        """Run data through all pipeline stages."""
        self._metrics.total_input += 1
        current = data

        for stage in self._stages:
            stage_name = stage.name
            start = time.monotonic()

            try:
                current = await stage.process(current)
            except Exception as e:
                logger.error("Pipeline stage %s error: %s", stage_name, e)
                self._metrics.total_errors += 1
                return None

            elapsed = (time.monotonic() - start) * 1000
            self._metrics.stage_times_ms[stage_name] = (
                self._metrics.stage_times_ms.get(stage_name, 0) + elapsed
            )

            if current is None:
                self._metrics.total_dropped += 1
                self._metrics.stage_drop_counts[stage_name] = (
                    self._metrics.stage_drop_counts.get(stage_name, 0) + 1
                )
                return None

        self._metrics.total_output += 1

        # Fire callbacks
        for cb in self._output_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(current)
                else:
                    cb(current)
            except Exception as e:
                logger.warning("Output callback error: %s", e)

        return current

    async def run_batch(self, items: list[Any]) -> list[Any]:
        """Run a batch of items, returning non-None results."""
        results = []
        for item in items:
            result = await self.run(item)
            if result is not None:
                results.append(result)
        return results

    @property
    def metrics(self) -> PipelineMetrics:
        return self._metrics

    @property
    def stage_count(self) -> int:
        return len(self._stages)

    def reset_metrics(self) -> None:
        self._metrics = PipelineMetrics()


def create_standard_pipeline() -> DataPipeline:
    """Create the standard game data processing pipeline."""
    pipeline = DataPipeline()
    pipeline.add_stage(ParseStage())
    pipeline.add_stage(ValidateStage())
    pipeline.add_stage(SnapshotStage())
    pipeline.add_stage(DeduplicateStage())
    return pipeline
