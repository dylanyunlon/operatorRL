#!/usr/bin/env python3
"""
CanbusTester — Standalone LCU API diagnostic tool.
=====================================================
lolbot-HyperAI · modules/canbus/tools

查看 Apollo ``modules/canbus/tools/canbus_tester.cc`` 上现有
``CanbusTester`` 的实现方式, 理解其模式, 特别是独立运行 + 交互式命令
+ CAN frame 收发测试的设计。从 Apollo canbus_tester 这个好例子开始。
然后, 遵循该模式实现一个新的 ``CanbusTester``, 让开发者可以独立测试
LCU API 连接, 并能验证各 endpoint 的响应格式。接着引入延迟测量, 使
开发者能够诊断 API 性能瓶颈, 同时优化输出格式为可读的诊断报告。

位置: lolbot-HyperAI/modules/canbus/tools/canbus_tester.py

Apollo reference:
    modules/canbus/tools/canbus_tester.cc — interactive CAN test

Usage:
    python -m modules.canbus.tools.canbus_tester
    python -m modules.canbus.tools.canbus_tester --url https://127.0.0.1:2999
    python -m modules.canbus.tools.canbus_tester --continuous --interval 0.1
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "https://127.0.0.1:2999"
_ENDPOINTS = (
    "/liveclientdata/allgamedata",
    "/liveclientdata/gamestats",
    "/liveclientdata/eventdata",
    "/liveclientdata/activeplayername",
    "/liveclientdata/activeplayerabilities",
    "/liveclientdata/playerlist",
)
_TIMEOUT_S = 3.0


# ── CanbusTester ─────────────────────────────────────────────────────────────

@dataclass
class EndpointResult:
    """Result of testing a single LCU endpoint."""

    endpoint: str = ""
    success: bool = False
    http_status: int = 0
    latency_ms: float = 0.0
    response_size: int = 0
    error: str = ""
    data_keys: List[str] = field(default_factory=list)
    data_preview: str = ""


class CanbusTester:
    """Standalone LCU API diagnostic tool.

    Apollo equivalent: ``canbus_tester.cc`` — tests CAN bus connectivity
    by sending/receiving frames. Our equivalent tests LCU HTTP API
    connectivity by probing all known endpoints.

    Can run in single-shot mode (test all endpoints once) or continuous
    mode (poll repeatedly at configurable interval).

    Usage::

        tester = CanbusTester(base_url="https://127.0.0.1:2999")
        report = tester.run_full_diagnostic()
        tester.print_report(report)
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_s: float = _TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Disable SSL verification for LCU self-signed cert
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def probe_endpoint(self, endpoint: str) -> EndpointResult:
        """Test a single LCU endpoint.

        Apollo equivalent: single CAN frame send+receive in canbus_tester.
        """
        result = EndpointResult(endpoint=endpoint)
        url = f"{self._base_url}{endpoint}"

        start = time.monotonic()
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(
                req, timeout=self._timeout_s, context=self._ssl_ctx
            ) as resp:
                body = resp.read()
                result.http_status = resp.status
                result.latency_ms = (time.monotonic() - start) * 1000.0
                result.response_size = len(body)
                result.success = 200 <= resp.status < 300

                # Parse JSON to extract structure info
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        result.data_keys = list(data.keys())[:10]
                        result.data_preview = json.dumps(
                            data, indent=None, default=str
                        )[:200]
                    elif isinstance(data, list):
                        result.data_keys = [f"list[{len(data)}]"]
                        result.data_preview = json.dumps(
                            data[:2], indent=None, default=str
                        )[:200]
                    elif isinstance(data, str):
                        result.data_keys = ["string"]
                        result.data_preview = data[:200]
                except (json.JSONDecodeError, ValueError):
                    result.data_preview = body.decode("utf-8", errors="replace")[:200]

        except urllib.error.HTTPError as exc:
            result.latency_ms = (time.monotonic() - start) * 1000.0
            result.http_status = exc.code
            result.error = f"HTTP {exc.code}: {exc.reason}"
        except urllib.error.URLError as exc:
            result.latency_ms = (time.monotonic() - start) * 1000.0
            result.error = f"Connection error: {exc.reason}"
        except Exception as exc:
            result.latency_ms = (time.monotonic() - start) * 1000.0
            result.error = f"{type(exc).__name__}: {exc}"

        return result

    def run_full_diagnostic(self) -> Dict[str, Any]:
        """Test all known LCU endpoints and produce a diagnostic report.

        Returns a dict with:
            - results: list of EndpointResult dicts
            - summary: overall connectivity status
            - timestamp: when the diagnostic was run
        """
        results: List[Dict[str, Any]] = []
        success_count = 0
        total_latency_ms = 0.0

        for endpoint in _ENDPOINTS:
            result = self.probe_endpoint(endpoint)
            results.append({
                "endpoint": result.endpoint,
                "success": result.success,
                "http_status": result.http_status,
                "latency_ms": round(result.latency_ms, 2),
                "response_size": result.response_size,
                "error": result.error,
                "data_keys": result.data_keys,
            })
            if result.success:
                success_count += 1
            total_latency_ms += result.latency_ms

        game_active = False
        game_time = 0.0
        for r in results:
            if r["endpoint"] == "/liveclientdata/gamestats" and r["success"]:
                game_active = True

        return {
            "timestamp": time.time(),
            "base_url": self._base_url,
            "endpoints_tested": len(_ENDPOINTS),
            "endpoints_passed": success_count,
            "total_latency_ms": round(total_latency_ms, 2),
            "avg_latency_ms": round(
                total_latency_ms / len(_ENDPOINTS) if _ENDPOINTS else 0, 2
            ),
            "game_active": game_active,
            "lcu_reachable": success_count > 0,
            "results": results,
        }

    def run_continuous(
        self,
        endpoint: str = "/liveclientdata/allgamedata",
        interval_s: float = 0.1,
        count: int = 100,
    ) -> Dict[str, Any]:
        """Continuous polling test — measure sustained latency/reliability.

        Apollo equivalent: continuous CAN frame send/receive loop.
        """
        latencies: List[float] = []
        errors: List[str] = []

        for i in range(count):
            result = self.probe_endpoint(endpoint)
            if result.success:
                latencies.append(result.latency_ms)
            else:
                errors.append(f"#{i}: {result.error}")
            time.sleep(interval_s)

        if latencies:
            latencies_sorted = sorted(latencies)
            n = len(latencies_sorted)
            stats = {
                "samples": n,
                "errors": len(errors),
                "success_rate": round(n / count * 100, 1),
                "mean_ms": round(sum(latencies) / n, 2),
                "min_ms": round(latencies_sorted[0], 2),
                "max_ms": round(latencies_sorted[-1], 2),
                "p50_ms": round(latencies_sorted[int(0.50 * n)], 2),
                "p95_ms": round(latencies_sorted[min(int(0.95 * n), n - 1)], 2),
                "p99_ms": round(latencies_sorted[min(int(0.99 * n), n - 1)], 2),
            }
        else:
            stats = {
                "samples": 0,
                "errors": len(errors),
                "success_rate": 0.0,
            }

        return {
            "endpoint": endpoint,
            "interval_s": interval_s,
            "total_polls": count,
            "stats": stats,
            "first_errors": errors[:5],
        }

    @staticmethod
    def print_report(report: Dict[str, Any]) -> None:
        """Pretty-print a diagnostic report to stdout."""
        print("=" * 65)
        print("  CanbusTester — LCU API Diagnostic Report")
        print("=" * 65)
        print(f"  Base URL:      {report['base_url']}")
        print(f"  LCU Reachable: {'YES' if report['lcu_reachable'] else 'NO'}")
        print(f"  Game Active:   {'YES' if report['game_active'] else 'NO'}")
        print(f"  Endpoints:     {report['endpoints_passed']}/{report['endpoints_tested']} passed")
        print(f"  Avg Latency:   {report['avg_latency_ms']} ms")
        print("-" * 65)

        for r in report["results"]:
            status = "OK" if r["success"] else "FAIL"
            print(f"  [{status:4s}] {r['endpoint']}")
            if r["success"]:
                print(f"         {r['latency_ms']:>7.1f} ms  |  {r['response_size']:>6} bytes  |  keys: {r['data_keys']}")
            else:
                print(f"         ERROR: {r['error']}")

        print("=" * 65)


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point for canbus_tester."""
    parser = argparse.ArgumentParser(
        description="CanbusTester — LCU API Diagnostic Tool"
    )
    parser.add_argument(
        "--url", type=str, default=_DEFAULT_BASE_URL,
        help=f"LCU base URL (default: {_DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--continuous", action="store_true",
        help="Run continuous polling test",
    )
    parser.add_argument(
        "--interval", type=float, default=0.1,
        help="Polling interval in seconds (default: 0.1)",
    )
    parser.add_argument(
        "--count", type=int, default=100,
        help="Number of polls in continuous mode (default: 100)",
    )
    parser.add_argument(
        "--endpoint", type=str, default="/liveclientdata/allgamedata",
        help="Endpoint for continuous test",
    )
    args = parser.parse_args()

    tester = CanbusTester(base_url=args.url)

    if args.continuous:
        print(f"Running continuous test: {args.count} polls at {args.interval}s interval...")
        result = tester.run_continuous(
            endpoint=args.endpoint,
            interval_s=args.interval,
            count=args.count,
        )
        print(json.dumps(result, indent=2))
    else:
        report = tester.run_full_diagnostic()
        tester.print_report(report)


if __name__ == "__main__":
    main()
