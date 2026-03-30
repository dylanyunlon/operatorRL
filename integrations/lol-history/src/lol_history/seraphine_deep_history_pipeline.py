"""
SeraphineDeepHistoryPipeline — Top-level pipeline orchestrating all M586-M603 modules.

Architecture (拿来主义):
  查看 **seraphine_history_orchestrator.py** 上现有 **M506-M524模块统一编排方式**，
  理解其模式，特别是register→initialize→run的生命周期如何与各子模块解耦。
  从 **agentos/governance/data_pipeline.py** 的 add_stage→run→get_results 链式调用开始。
  实现 **SeraphineDeepHistoryPipeline**，让 **所有M586-M603模块** 可以 **通过统一管线
  接口处理match_history数据**，并能 **隔离模块故障、追踪每个模块耗时、通过evolution_callback
  上报管线事件**。

Location: integrations/lol-history/src/lol_history/seraphine_deep_history_pipeline.py

Design Notes (Knuth-level critique):
  User:
    - Module error isolation ensures one broken analyzer doesn't crash the pipeline.
    - Timing per module helps identify slow stages.
    - Pipeline returns all results plus overall status for downstream consumers.
  System:
    - Module functions receive raw match_history list — they are pure transforms.
    - Last result caching enables incremental debugging without re-running.
    - Empty input short-circuits cleanly without invoking any module.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.seraphine_deep_history_pipeline.v1"


class SeraphineDeepHistoryPipeline:
    """Top-level pipeline orchestrating all Seraphine deep history modules.

    Public API
    ----------
    register_module(name, func) — Register an analysis module.
    list_modules() -> list[str]
    run(match_history) -> dict — Execute all modules on match history data.
    get_last_result(module_name) -> Any
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._modules: Dict[str, Callable] = {}
        self._module_order: List[str] = []
        self._last_results: Dict[str, Any] = {}
        self._run_count: int = 0

    def register_module(self, name: str, func: Callable[[List[Dict[str, Any]]], Dict[str, Any]]) -> None:
        """Register an analysis module.

        Parameters
        ----------
        name : str
            Module name (unique identifier).
        func : callable
            Function that takes match_history list and returns a result dict.
        """
        self._modules[name] = func
        if name not in self._module_order:
            self._module_order.append(name)

    def list_modules(self) -> List[str]:
        """Return list of registered module names in registration order."""
        return list(self._module_order)

    def run(self, match_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute all registered modules on the match history.

        Parameters
        ----------
        match_history : list[dict]
            Raw match history data.

        Returns
        -------
        dict with status, modules results, elapsed_ms, and errors.
        """
        self._run_count += 1

        if not match_history:
            result = {
                "status": "empty",
                "modules": {},
                "elapsed_ms": 0.0,
                "run_count": self._run_count,
            }
            self._fire("pipeline_run", {"status": "empty"})
            return result

        start = time.time()
        module_results: Dict[str, Any] = {}
        errors: List[str] = []

        for name in self._module_order:
            func = self._modules[name]
            mod_start = time.time()
            try:
                mod_result = func(match_history)
                module_results[name] = mod_result
                self._last_results[name] = mod_result
            except Exception as exc:
                err_msg = f"{name}: {type(exc).__name__}: {exc}"
                logger.warning("Module %s failed: %s", name, exc)
                module_results[name] = {"error": str(exc)}
                self._last_results[name] = {"error": str(exc)}
                errors.append(err_msg)
            finally:
                mod_elapsed = (time.time() - mod_start) * 1000
                if name in module_results and isinstance(module_results[name], dict):
                    module_results[name]["_elapsed_ms"] = mod_elapsed

        elapsed = (time.time() - start) * 1000
        status = "ok" if not errors else "partial"

        result = {
            "status": status,
            "modules": module_results,
            "elapsed_ms": elapsed,
            "run_count": self._run_count,
            "errors": errors,
        }
        self._fire("pipeline_run", {
            "status": status,
            "module_count": len(self._module_order),
            "error_count": len(errors),
            "elapsed_ms": elapsed,
        })
        return result

    def get_last_result(self, module_name: str) -> Any:
        """Get the last cached result for a module."""
        return self._last_results.get(module_name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modules": self._module_order,
            "run_count": self._run_count,
            "last_results_keys": list(self._last_results.keys()),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback:
            self.evolution_callback({
                "type": event_type,
                "key": _EVOLUTION_KEY,
                "timestamp": time.time(),
                **data,
            })
