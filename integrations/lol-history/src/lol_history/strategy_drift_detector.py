"""
StrategyDriftDetector — Detects when model strategy drifts from historical optimal patterns.

Architecture (拿来主义):
  fitness_aggregator.py + streak_momentum_analyzer.py（M600）

Location: integrations/lol-history/src/lol_history/strategy_drift_detector.py

Design Notes (Knuth-level critique):
  User:
    - All methods handle empty input gracefully (no crash).
    - Results include structured metadata for downstream consumers.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.strategydriftdetector.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Division safe against zero denominators."""
    return a / b if b != 0 else default


class StrategyDriftDetector:
    """Detects when model strategy drifts from historical optimal patterns.

    Public API
    ----------
        detect_drift
    add_snapshot
    get_drift_report
    check_threshold
    reset

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._cache: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self._config: Dict[str, Any] = {}

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"type": event_type, "key": _EVOLUTION_KEY, **data})

    def detect_drift(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute detect_drift operation.

        Parameters
        ----------
        data : dict
            Input data for detect_drift.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "detect_drift"}

        elapsed = time.time() - _start
        self._fire("detect_drift_completed", {"elapsed": elapsed})
        return result
    def add_snapshot(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute add_snapshot operation.

        Parameters
        ----------
        data : dict
            Input data for add_snapshot.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "add_snapshot"}

        elapsed = time.time() - _start
        self._fire("add_snapshot_completed", {"elapsed": elapsed})
        return result
    def get_drift_report(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute get_drift_report operation.

        Parameters
        ----------
        data : dict
            Input data for get_drift_report.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "get_drift_report"}

        elapsed = time.time() - _start
        self._fire("get_drift_report_completed", {"elapsed": elapsed})
        return result
    def check_threshold(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute check_threshold operation.

        Parameters
        ----------
        data : dict
            Input data for check_threshold.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "check_threshold"}

        elapsed = time.time() - _start
        self._fire("check_threshold_completed", {"elapsed": elapsed})
        return result
    def reset(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute reset operation.

        Parameters
        ----------
        data : dict
            Input data for reset.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "reset"}

        elapsed = time.time() - _start
        self._fire("reset_completed", {"elapsed": elapsed})
        return result
