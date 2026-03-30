"""
Inference Pipeline Builder — Compose inference stages into pipelines.

Provides a fluent builder API for constructing inference pipelines from
discrete stages (preprocess, encode, forward, sample, postprocess).
Supports conditional stages, branching, and pipeline serialization.

Location: agentlightning/inference/inference_pipeline_builder.py

Reference (拿来主义):
  查看 agentos/governance/data_pipeline.py 上现有 DataPipeline 的
  stage注册和执行方式, 理解其模式, 特别是 add_stage→run→get_results
  的链式调用如何与各stage的实际逻辑分离。
  从 agentlightning/adapter/base.py 这个好例子开始 — 它的
  Adapter[T_from, T_to]泛型设计展示了如何用统一接口包装不同实现。
  遵循该模式实现 InferencePipelineBuilder, 让所有game agent可以
  通过统一的pipeline配置来组装推理流程, 并能在运行时动态插入/移除阶段.

Design Notes (Knuth-level critique):
  User:
    - Fluent builder API makes pipeline composition readable
    - Named stages enable targeted debugging and profiling
    - Conditional stages skip unnecessary computation
  System:
    - Stages are just Callable — zero framework coupling
    - Pipeline is serializable to dict for persistence/replay
    - Error in one stage doesn't corrupt downstream state
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.inference_pipeline_builder.v1"


class PipelineStage:
    """A single stage in an inference pipeline.

    Attributes:
        name: Stage identifier.
        fn: Stage callable(input_data) → output_data.
        condition: Optional predicate; stage skipped if returns False.
        error_handler: Optional handler for stage errors.
    """

    __slots__ = ("name", "fn", "condition", "error_handler", "enabled")

    def __init__(
        self,
        name: str,
        fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None,
        error_handler: Optional[Callable[[Exception, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self.name = name
        self.fn = fn
        self.condition = condition
        self.error_handler = error_handler
        self.enabled: bool = True

    def should_run(self, data: Dict[str, Any]) -> bool:
        """Check if this stage should execute."""
        if not self.enabled:
            return False
        if self.condition is not None:
            return self.condition(data)
        return True

    def execute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the stage.

        Args:
            data: Pipeline data dict (mutable, passed through stages).

        Returns:
            Updated data dict.

        Raises:
            Exception: If stage fails and no error_handler.
        """
        try:
            return self.fn(data)
        except Exception as exc:
            if self.error_handler is not None:
                return self.error_handler(exc, data)
            raise

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "has_condition": self.condition is not None,
            "has_error_handler": self.error_handler is not None,
        }


class PipelineResult:
    """Result of a pipeline execution.

    Attributes:
        data: Final output data dict.
        stage_timings: Per-stage latency in ms.
        skipped_stages: Stages that were skipped.
        total_ms: Total pipeline latency.
        success: Whether pipeline completed without error.
        error: Error message if failed.
    """

    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self.stage_timings: Dict[str, float] = {}
        self.skipped_stages: List[str] = []
        self.total_ms: float = 0.0
        self.success: bool = True
        self.error: Optional[str] = None
        self.failed_stage: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "total_ms": round(self.total_ms, 3),
            "stage_timings": {k: round(v, 3) for k, v in self.stage_timings.items()},
            "skipped_stages": self.skipped_stages,
            "error": self.error,
            "failed_stage": self.failed_stage,
        }


class InferencePipelineBuilder:
    """Fluent builder for inference pipelines.

    Usage:
        pipeline = (InferencePipelineBuilder("lol_inference")
            .add_stage("preprocess", preprocess_fn)
            .add_stage("encode", encode_fn)
            .add_stage("forward", forward_fn)
            .add_stage("sample", sample_fn, condition=lambda d: d.get("use_nn"))
            .add_stage("postprocess", postprocess_fn)
            .build())

        result = pipeline.run({"game_state": state})

    Attributes:
        name: Pipeline name.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._stages: List[PipelineStage] = []
        self._built: bool = False
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Builder API ---

    def add_stage(
        self,
        name: str,
        fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None,
        error_handler: Optional[Callable[[Exception, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> "InferencePipelineBuilder":
        """Add a stage to the pipeline.

        Args:
            name: Stage identifier.
            fn: Stage function.
            condition: Optional predicate for conditional execution.
            error_handler: Optional error handler.

        Returns:
            self for chaining.
        """
        stage = PipelineStage(name, fn, condition, error_handler)
        self._stages.append(stage)
        return self

    def insert_stage(
        self,
        index: int,
        name: str,
        fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> "InferencePipelineBuilder":
        """Insert a stage at a specific position.

        Args:
            index: Position to insert at.
            name: Stage identifier.
            fn: Stage function.
            condition: Optional predicate.

        Returns:
            self for chaining.
        """
        stage = PipelineStage(name, fn, condition)
        self._stages.insert(index, stage)
        return self

    def remove_stage(self, name: str) -> "InferencePipelineBuilder":
        """Remove a stage by name.

        Args:
            name: Stage identifier to remove.

        Returns:
            self for chaining.
        """
        self._stages = [s for s in self._stages if s.name != name]
        return self

    def disable_stage(self, name: str) -> "InferencePipelineBuilder":
        """Disable a stage without removing it.

        Args:
            name: Stage identifier.

        Returns:
            self for chaining.
        """
        for s in self._stages:
            if s.name == name:
                s.enabled = False
        return self

    def enable_stage(self, name: str) -> "InferencePipelineBuilder":
        """Enable a previously disabled stage.

        Args:
            name: Stage identifier.

        Returns:
            self for chaining.
        """
        for s in self._stages:
            if s.name == name:
                s.enabled = True
        return self

    def build(self) -> "InferencePipeline":
        """Build the pipeline.

        Returns:
            Immutable InferencePipeline ready for execution.
        """
        pipeline = InferencePipeline(
            name=self.name,
            stages=list(self._stages),
        )
        pipeline.evolution_callback = self.evolution_callback
        self._built = True
        return pipeline

    def stage_names(self) -> List[str]:
        """Get ordered list of stage names."""
        return [s.name for s in self._stages]

    def stage_count(self) -> int:
        """Number of stages."""
        return len(self._stages)

    def describe(self) -> Dict[str, Any]:
        """Describe the pipeline configuration."""
        return {
            "name": self.name,
            "stage_count": len(self._stages),
            "stages": [s.to_dict() for s in self._stages],
        }


class InferencePipeline:
    """Executable inference pipeline.

    Created by InferencePipelineBuilder.build(). Runs data through
    a sequence of stages with timing and error handling.
    """

    def __init__(
        self,
        name: str,
        stages: List[PipelineStage],
    ) -> None:
        self.name = name
        self._stages = stages
        self._run_count: int = 0
        self._total_ms: float = 0.0
        self._error_count: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def run(self, data: Dict[str, Any]) -> PipelineResult:
        """Execute the pipeline.

        Args:
            data: Input data dict. Modified in-place through stages.

        Returns:
            PipelineResult with output data and timing.
        """
        result = PipelineResult()
        pipeline_start = time.monotonic()

        current_data = dict(data)  # shallow copy to avoid mutating input

        for stage in self._stages:
            if not stage.should_run(current_data):
                result.skipped_stages.append(stage.name)
                continue

            stage_start = time.monotonic()
            try:
                current_data = stage.execute(current_data)
                elapsed_ms = (time.monotonic() - stage_start) * 1000.0
                result.stage_timings[stage.name] = elapsed_ms
            except Exception as exc:
                elapsed_ms = (time.monotonic() - stage_start) * 1000.0
                result.stage_timings[stage.name] = elapsed_ms
                result.success = False
                result.error = str(exc)
                result.failed_stage = stage.name
                self._error_count += 1
                break

        result.data = current_data
        result.total_ms = (time.monotonic() - pipeline_start) * 1000.0
        self._run_count += 1
        self._total_ms += result.total_ms

        self._fire_evolution("pipeline_executed", {
            "pipeline": self.name,
            "success": result.success,
            "total_ms": result.total_ms,
            "stages_run": len(result.stage_timings),
            "stages_skipped": len(result.skipped_stages),
        })

        return result

    def stage_names(self) -> List[str]:
        """Get ordered stage names."""
        return [s.name for s in self._stages]

    def stage_count(self) -> int:
        """Number of stages."""
        return len(self._stages)

    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline execution statistics."""
        return {
            "name": self.name,
            "run_count": self._run_count,
            "error_count": self._error_count,
            "avg_ms": round(self._total_ms / max(self._run_count, 1), 3),
            "total_ms": round(self._total_ms, 3),
        }

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
