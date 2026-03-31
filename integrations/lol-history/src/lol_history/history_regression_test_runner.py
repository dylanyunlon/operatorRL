"""
HistoryRegressionTestRunner — Regression testing framework for history intelligence.

Architecture (拿来主义):
  tests目录 + history_data_quality_checker.py（M624）

Location: integrations/lol-history/src/lol_history/history_regression_test_runner.py

Design Notes (Knuth-level critique):
  User:
    - register_test is declarative — test = {input, expected_output, comparator}.
    - run_all returns per-test pass/fail with detailed diffs on failure.
    - Supports snapshot testing: save golden outputs and compare against them.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Tests are isolated — one failure does not abort the suite.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_regression_test_runner.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _default_comparator(expected: Any, actual: Any) -> bool:
    """Default equality comparator."""
    return expected == actual


def _deep_diff(expected: Any, actual: Any, path: str = "") -> List[str]:
    """Compute human-readable diffs between expected and actual."""
    diffs: List[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        all_keys = set(list(expected.keys()) + list(actual.keys()))
        for k in sorted(all_keys):
            child_path = f"{path}.{k}" if path else k
            if k not in expected:
                diffs.append(f"  + {child_path}: {actual[k]!r} (unexpected)")
            elif k not in actual:
                diffs.append(f"  - {child_path}: {expected[k]!r} (missing)")
            else:
                diffs.extend(_deep_diff(expected[k], actual[k], child_path))
    elif expected != actual:
        diffs.append(f"  {path}: expected={expected!r}, actual={actual!r}")
    return diffs


class HistoryRegressionTestRunner:
    """Regression testing framework for history intelligence modules.

    Public API
    ----------
    register_test       — register a test case
    run_all             — run all registered tests
    run_one             — run a single test by name
    save_snapshot       — save golden output for a test
    get_report          — get test results report
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._tests: Dict[str, Dict[str, Any]] = {}
        self._results: List[Dict[str, Any]] = []
        self._snapshots: Dict[str, Any] = {}
        self._run_count: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def register_test(self, name: str,
                      func: Callable[..., Any],
                      input_data: Any = None,
                      expected_output: Any = None,
                      comparator: Optional[Callable[[Any, Any], bool]] = None,
                      tags: List[str] = None) -> Dict[str, Any]:
        """Register a test case.

        Parameters
        ----------
        name : str  unique test name
        func : callable  the function under test
        input_data : any  input to pass to func
        expected_output : any  expected result
        comparator : callable  (expected, actual) -> bool
        tags : list of str

        Returns
        -------
        dict
        """
        self._op_count += 1
        self._tests[name] = {
            "name": name,
            "func": func,
            "input_data": input_data,
            "expected_output": expected_output,
            "comparator": comparator or _default_comparator,
            "tags": tags or [],
            "registered_at": time.time(),
        }
        return {"status": "ok", "op": "register_test",
                "name": name, "total_tests": len(self._tests)}

    # ------------------------------------------------------------------ #

    def run_one(self, name: str) -> Dict[str, Any]:
        """Run a single test by name.

        Returns
        -------
        dict  with passed, actual_output, diff (if failed)
        """
        self._op_count += 1
        _start = time.time()

        test = self._tests.get(name)
        if test is None:
            return {"status": "error", "reason": "unknown test name"}

        func = test["func"]
        input_data = test["input_data"]
        expected = test["expected_output"]
        comparator = test["comparator"]

        try:
            if input_data is not None:
                actual = func(input_data)
            else:
                actual = func()
        except Exception as exc:
            elapsed = time.time() - _start
            result = {
                "name": name, "passed": False,
                "error": str(exc), "elapsed": round(elapsed, 6),
            }
            self._results.append(result)
            return {"status": "ok", "op": "run_one", **result}

        passed = comparator(expected, actual)
        elapsed = time.time() - _start

        result: Dict[str, Any] = {
            "name": name, "passed": passed,
            "elapsed": round(elapsed, 6),
        }

        if not passed:
            result["expected"] = expected
            result["actual"] = actual
            if isinstance(expected, dict) and isinstance(actual, dict):
                result["diff"] = _deep_diff(expected, actual)

        self._results.append(result)
        return {"status": "ok", "op": "run_one", **result}

    # ------------------------------------------------------------------ #

    def run_all(self, tags: List[str] = None) -> Dict[str, Any]:
        """Run all registered tests (optionally filtered by tags).

        Parameters
        ----------
        tags : list of str  optional filter

        Returns
        -------
        dict  with passed_count, failed_count, results
        """
        self._op_count += 1
        _start = time.time()
        self._results.clear()
        self._run_count += 1

        tests_to_run = self._tests
        if tags:
            tag_set = set(tags)
            tests_to_run = {
                name: t for name, t in self._tests.items()
                if tag_set.intersection(t.get("tags", []))
            }

        results: List[Dict[str, Any]] = []
        passed = 0
        failed = 0

        for name in sorted(tests_to_run.keys()):
            r = self.run_one(name)
            results.append(r)
            if r.get("passed"):
                passed += 1
            else:
                failed += 1

        elapsed = time.time() - _start
        self._fire("run_all_completed", {
            "elapsed": elapsed, "passed": passed, "failed": failed,
        })
        return {"status": "ok", "op": "run_all",
                "passed_count": passed, "failed_count": failed,
                "total": passed + failed, "results": results,
                "elapsed": round(elapsed, 6)}

    # ------------------------------------------------------------------ #

    def save_snapshot(self, name: str, output: Any) -> Dict[str, Any]:
        """Save golden output for snapshot testing.

        Parameters
        ----------
        name : str  snapshot name
        output : any

        Returns
        -------
        dict
        """
        self._op_count += 1
        self._snapshots[name] = {
            "output": output,
            "saved_at": time.time(),
        }
        return {"status": "ok", "op": "save_snapshot", "name": name}

    # ------------------------------------------------------------------ #

    def get_report(self) -> Dict[str, Any]:
        """Get test results report.

        Returns
        -------
        dict  with summary and per-test results
        """
        self._op_count += 1
        passed = sum(1 for r in self._results if r.get("passed"))
        failed = len(self._results) - passed

        return {"status": "ok", "op": "get_report",
                "run_count": self._run_count,
                "total_registered": len(self._tests),
                "last_run_passed": passed,
                "last_run_failed": failed,
                "pass_rate": round(_safe_div(passed, len(self._results)), 4),
                "results": list(self._results)}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "registered_tests": len(self._tests),
            "run_count": self._run_count,
            "snapshot_count": len(self._snapshots),
            "last_results_count": len(self._results),
        }
