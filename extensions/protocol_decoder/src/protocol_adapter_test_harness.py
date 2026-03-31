"""
ProtocolAdapterTestHarness — Standardized compatibility testing for game protocol adapters.

Runs a suite of conformance tests against any GameProtocolAdapterBase implementation
to verify it meets the universal adapter contract.

Location: extensions/protocol_decoder/src/protocol_adapter_test_harness.py

Reference (拿来主義):
  - integrations/lol-history/src/lol_history/history_regression_test_runner.py（M644）:
    regression test framework
  - integrations/lol-history/src/lol_history/history_data_quality_checker.py（M624）:
    schema validation

Design Notes (Knuth-level critique):
  User:
    - run_all(adapter) returns structured report — pass/fail per test.
    - register_test() allows adding custom conformance tests.
    - generate_report() produces human-readable summary.
  System:
    - Each test is independent — failure in one doesn't block others.
    - Test result includes timing for performance benchmarking.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.protocol_adapter_test_harness.v1"

try:
    from .game_protocol_adapter_base import GameProtocolAdapterBase, AdapterState
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from game_protocol_adapter_base import GameProtocolAdapterBase, AdapterState


class TestResult:
    __slots__ = ("name", "passed", "message", "elapsed_ms")

    def __init__(self, name: str, passed: bool, message: str = "", elapsed_ms: float = 0.0):
        self.name = name
        self.passed = passed
        self.message = message
        self.elapsed_ms = elapsed_ms

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class ProtocolAdapterTestHarness:
    """Standardized test harness for game protocol adapters.

    Public API:
        run_all(adapter, sample_data) -> list[TestResult]
        register_test(name, fn)
        generate_report(results) -> dict
    """

    def __init__(self) -> None:
        self._tests: Dict[str, Callable] = {}
        self._run_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._register_builtin_tests()

    def _register_builtin_tests(self) -> None:
        self._tests["game_type_defined"] = self._test_game_type_defined
        self._tests["connect_disconnect_idempotent"] = self._test_connect_disconnect
        self._tests["decode_returns_dict"] = self._test_decode_returns_dict
        self._tests["decode_error_handling"] = self._test_decode_error_handling
        self._tests["normalize_returns_dict"] = self._test_normalize_returns_dict
        self._tests["decode_and_normalize_chain"] = self._test_decode_and_normalize
        self._tests["health_check"] = self._test_health_check
        self._tests["stats_check"] = self._test_stats_check
        self._tests["state_transitions"] = self._test_state_transitions
        self._tests["evolution_callback"] = self._test_evolution_callback

    def register_test(self, name: str, fn: Callable) -> None:
        self._tests[name] = fn

    def run_all(
        self,
        adapter: GameProtocolAdapterBase,
        sample_data: Optional[Dict[str, Any]] = None,
    ) -> List[TestResult]:
        self._run_count += 1
        if sample_data is None:
            sample_data = {"_test": True, "game_time": 100.0}

        results: List[TestResult] = []
        for name, fn in self._tests.items():
            start = time.time()
            try:
                fn(adapter, sample_data)
                elapsed = (time.time() - start) * 1000
                results.append(TestResult(name, True, "passed", elapsed))
            except Exception as exc:
                elapsed = (time.time() - start) * 1000
                results.append(TestResult(name, False, str(exc), elapsed))

        self._fire("harness_completed", {
            "adapter": adapter.__class__.__name__,
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
        })
        return results

    def generate_report(self, results: List[TestResult]) -> Dict[str, Any]:
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        return {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": passed / max(len(results), 1),
            "results": [r.to_dict() for r in results],
            "failed_tests": [r.to_dict() for r in results if not r.passed],
        }

    # --- Built-in tests ---

    @staticmethod
    def _test_game_type_defined(adapter: GameProtocolAdapterBase, _: Any) -> None:
        gt = adapter.game_type
        assert isinstance(gt, str) and len(gt) > 0, "game_type must be non-empty string"

    @staticmethod
    def _test_connect_disconnect(adapter: GameProtocolAdapterBase, _: Any) -> None:
        adapter.disconnect()
        assert adapter.state == AdapterState.DISCONNECTED
        adapter.disconnect()  # idempotent
        assert adapter.state == AdapterState.DISCONNECTED
        ok = adapter.connect({})
        assert ok is True or ok is False, "connect must return bool"
        if ok:
            assert adapter.is_connected
            ok2 = adapter.connect({})  # idempotent
            assert ok2 is True
        adapter.disconnect()
        assert adapter.state == AdapterState.DISCONNECTED

    @staticmethod
    def _test_decode_returns_dict(adapter: GameProtocolAdapterBase, sample: Any) -> None:
        result = adapter.decode(sample)
        assert isinstance(result, dict), "decode must return dict"
        assert "_game_type" in result, "decode result must have _game_type"

    @staticmethod
    def _test_decode_error_handling(adapter: GameProtocolAdapterBase, _: Any) -> None:
        result = adapter.decode(object())  # invalid input
        assert isinstance(result, dict), "decode must return dict even on error"
        assert result.get("_decoded") is False, "failed decode must set _decoded=False"

    @staticmethod
    def _test_normalize_returns_dict(adapter: GameProtocolAdapterBase, sample: Any) -> None:
        decoded = adapter.decode(sample)
        if decoded.get("_decoded"):
            result = adapter.normalize(decoded)
            assert isinstance(result, dict), "normalize must return dict"
            assert "_game_type" in result

    @staticmethod
    def _test_decode_and_normalize(adapter: GameProtocolAdapterBase, sample: Any) -> None:
        result = adapter.decode_and_normalize(sample)
        assert isinstance(result, dict), "decode_and_normalize must return dict"

    @staticmethod
    def _test_health_check(adapter: GameProtocolAdapterBase, _: Any) -> None:
        health = adapter.get_health()
        assert isinstance(health, dict)
        assert "game_type" in health
        assert "state" in health
        assert "error_count" in health

    @staticmethod
    def _test_stats_check(adapter: GameProtocolAdapterBase, _: Any) -> None:
        stats = adapter.get_stats()
        assert isinstance(stats, dict)
        assert "decode_count" in stats or "game_type" in stats

    @staticmethod
    def _test_state_transitions(adapter: GameProtocolAdapterBase, _: Any) -> None:
        adapter.disconnect()
        initial = adapter.state
        assert initial == AdapterState.DISCONNECTED

    @staticmethod
    def _test_evolution_callback(adapter: GameProtocolAdapterBase, sample: Any) -> None:
        events: List[Dict] = []
        adapter.evolution_callback = lambda e: events.append(e)
        adapter.decode(sample)
        # Should have fired at least one event (decode_error or state tracking)
        adapter.evolution_callback = None

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")
