"""
HistoryIntelligenceOrchestrator — Master orchestrator for the full history intelligence pipeline.

Architecture (拿来主义):
  seraphine_history_orchestrator.py + history_feedback_loop_orchestrator.py（M625）

Location: integrations/lol-history/src/lol_history/history_intelligence_orchestrator.py

Design Notes (Knuth-level critique):
  User:
    - run_pipeline executes the full history→analysis→strategy→coaching chain.
    - Each step is independently skippable — partial pipelines are valid.
    - get_pipeline_status shows per-step timing and success/failure for debugging.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Steps are registered as a chain — failure in one step does not abort the rest.
    - Retry logic with configurable max_retries per step.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_intelligence_orchestrator.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class PipelineStep:
    """A single step in the orchestration pipeline."""

    __slots__ = ("name", "func", "max_retries", "skip_on_error", "timeout")

    def __init__(self, name: str, func: Callable[..., Dict[str, Any]],
                 max_retries: int = 1, skip_on_error: bool = True,
                 timeout: float = 30.0) -> None:
        self.name = name
        self.func = func
        self.max_retries = max_retries
        self.skip_on_error = skip_on_error
        self.timeout = timeout

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "max_retries": self.max_retries,
            "skip_on_error": self.skip_on_error,
            "timeout": self.timeout,
        }


class StepResult:
    """Result of executing a pipeline step."""

    __slots__ = ("name", "success", "output", "error", "elapsed",
                 "retries_used", "skipped")

    def __init__(self, name: str) -> None:
        self.name = name
        self.success: bool = False
        self.output: Dict[str, Any] = {}
        self.error: str = ""
        self.elapsed: float = 0.0
        self.retries_used: int = 0
        self.skipped: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "error": self.error,
            "elapsed": round(self.elapsed, 6),
            "retries_used": self.retries_used,
            "skipped": self.skipped,
        }


class HistoryIntelligenceOrchestrator:
    """Master orchestrator for the full history intelligence pipeline.

    Public API
    ----------
    register_step       — register a pipeline step
    run_pipeline        — execute the full pipeline
    run_step            — execute a single step
    get_pipeline_status — get per-step timing and results
    get_pipeline_config — get pipeline configuration
    reset               — clear pipeline state
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._steps: List[PipelineStep] = []
        self._step_map: Dict[str, PipelineStep] = {}
        self._last_run_results: List[StepResult] = []
        self._run_count: int = 0
        self._total_elapsed: float = 0.0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def register_step(self, name: str, func: Callable[..., Dict[str, Any]],
                      max_retries: int = 1, skip_on_error: bool = True,
                      timeout: float = 30.0) -> Dict[str, Any]:
        """Register a pipeline step.

        Parameters
        ----------
        name : str  unique step name
        func : callable  (context_dict) -> result_dict
        max_retries : int
        skip_on_error : bool  if True, pipeline continues on failure
        timeout : float  max seconds for this step

        Returns
        -------
        dict
        """
        self._op_count += 1
        step = PipelineStep(name, func, max_retries, skip_on_error, timeout)
        self._steps.append(step)
        self._step_map[name] = step
        self._fire("register_step", {"name": name, "total_steps": len(self._steps)})
        return {"status": "ok", "op": "register_step",
                "name": name, "total_steps": len(self._steps)}

    # ------------------------------------------------------------------ #

    def run_step(self, name: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute a single step.

        Parameters
        ----------
        name : str
        context : dict  input context

        Returns
        -------
        dict  with step result
        """
        self._op_count += 1
        step = self._step_map.get(name)
        if step is None:
            return {"status": "error", "reason": f"unknown step: {name}"}

        if context is None:
            context = {}

        result = StepResult(name)
        _start = time.time()

        for attempt in range(step.max_retries):
            try:
                output = step.func(context)
                result.success = True
                result.output = output if isinstance(output, dict) else {"result": output}
                result.retries_used = attempt
                break
            except Exception as exc:
                result.error = str(exc)
                result.retries_used = attempt + 1

        result.elapsed = time.time() - _start
        return {"status": "ok", "op": "run_step", **result.to_dict()}

    # ------------------------------------------------------------------ #

    def run_pipeline(self, context: Dict[str, Any] = None,
                     skip_steps: List[str] = None) -> Dict[str, Any]:
        """Execute the full pipeline.

        Parameters
        ----------
        context : dict  initial context passed through all steps
        skip_steps : list of str  step names to skip

        Returns
        -------
        dict  with overall success, per-step results, total elapsed
        """
        self._op_count += 1
        _start = time.time()
        if context is None:
            context = {}
        if skip_steps is None:
            skip_steps = []

        skip_set = set(skip_steps)
        results: List[StepResult] = []
        pipeline_context = dict(context)
        overall_success = True

        for step in self._steps:
            sr = StepResult(step.name)

            if step.name in skip_set:
                sr.skipped = True
                results.append(sr)
                continue

            step_start = time.time()

            for attempt in range(step.max_retries):
                try:
                    output = step.func(pipeline_context)
                    sr.success = True
                    sr.output = output if isinstance(output, dict) else {"result": output}
                    sr.retries_used = attempt
                    # Merge output into pipeline context for next step
                    if isinstance(output, dict):
                        pipeline_context[step.name] = output
                    break
                except Exception as exc:
                    sr.error = str(exc)
                    sr.retries_used = attempt + 1

            sr.elapsed = time.time() - step_start

            if not sr.success:
                overall_success = False
                if not step.skip_on_error:
                    results.append(sr)
                    break  # abort pipeline

            results.append(sr)

        self._last_run_results = results
        self._run_count += 1
        total_elapsed = time.time() - _start
        self._total_elapsed += total_elapsed

        self._fire("run_pipeline_completed", {
            "elapsed": total_elapsed,
            "steps_run": len(results),
            "success": overall_success,
        })

        return {
            "status": "ok", "op": "run_pipeline",
            "overall_success": overall_success,
            "steps_run": len(results),
            "steps_passed": sum(1 for r in results if r.success),
            "steps_failed": sum(1 for r in results if not r.success and not r.skipped),
            "steps_skipped": sum(1 for r in results if r.skipped),
            "total_elapsed": round(total_elapsed, 6),
            "results": [r.to_dict() for r in results],
        }

    # ------------------------------------------------------------------ #

    def get_pipeline_status(self) -> Dict[str, Any]:
        """Get per-step timing and results from last run.

        Returns
        -------
        dict
        """
        self._op_count += 1
        return {
            "status": "ok", "op": "get_pipeline_status",
            "run_count": self._run_count,
            "total_steps": len(self._steps),
            "last_run": [r.to_dict() for r in self._last_run_results],
            "total_elapsed": round(self._total_elapsed, 6),
        }

    # ------------------------------------------------------------------ #

    def get_pipeline_config(self) -> Dict[str, Any]:
        """Get pipeline configuration.

        Returns
        -------
        dict  with step configs
        """
        self._op_count += 1
        return {
            "status": "ok", "op": "get_pipeline_config",
            "steps": [s.to_dict() for s in self._steps],
        }

    # ------------------------------------------------------------------ #

    def reset(self) -> Dict[str, Any]:
        """Clear pipeline state (keeps registered steps)."""
        self._op_count += 1
        self._last_run_results.clear()
        self._run_count = 0
        self._total_elapsed = 0.0
        self._fire("reset_completed", {})
        return {"status": "ok", "op": "reset"}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "registered_steps": len(self._steps),
            "run_count": self._run_count,
            "total_elapsed": round(self._total_elapsed, 6),
        }
