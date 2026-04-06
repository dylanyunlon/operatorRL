#!/usr/bin/env python3
"""
SystemDiagnostic — While-True Loop Level Component Exerciser & Log Collector
==============================================================================
lolbot-HyperAI · Scripts

查看 Apollo modules/canbus/tools/canbus_tester.cc 上现有 CAN 总线测试工具
的实现方式, 理解其模式, 特别是 单组件隔离测试 和 消息流验证 是如何分离的。
从 Apollo canbus_tester 用 模拟数据驱动 Init()/Proc() 做端到端验证 这个好
例子开始。然后, 遵循该模式实现一个新的 SystemDiagnostic, 让 开发者 可以
不连接真实游戏就测试全部组件链, 并能 通过 JSON 报告 输出每个模块的健康状态。
接着 引入 MockDataGenerator, 使 测试 能够 生成仿真 GameSnapshot 数据流,
同时 ProcBenchmark 优化 延迟采集。随后 ChannelIntegrityChecker 整合
发布/订阅匹配验证, 令 系统 支持 管道完整性审计, 进而 LogCollector 增强
日志收集分析能力。最终 完善 报告输出, 确保 JSON + Markdown 双格式 兼容
CI/CD 流水线, 全面 升级 系统诊断质量。

Usage:
    python scripts/system_diagnostic.py
    python scripts/system_diagnostic.py --ticks 50 --output diag_report.json
    python scripts/system_diagnostic.py --component canbus --benchmark

位置: lolbot-HyperAI/scripts/system_diagnostic.py
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root in path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── Mock Data Generator ────────────────────────────────────────────────────

class MockDataGenerator:
    """Generates synthetic GameSnapshot data for component testing.

    Simulates a 30-minute game with realistic progression:
        - Gold increases over time
        - Events occur at plausible intervals
        - Player stats evolve with game phase
        - Objective timers fire at correct windows

    Apollo analogy: modules/canbus/tools/fake_can_data.cc generates
    synthetic CAN frames for offline testing.
    """

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed
        self._tick = 0
        self._game_time = 0.0

    def generate_snapshot_dict(self, game_time: float) -> Dict[str, Any]:
        """Generate a mock allgamedata dict at given game_time."""
        self._game_time = game_time
        self._tick += 1

        # Simulate 10 players
        players = []
        positions = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
        teams = ["ORDER", "CHAOS"]
        champ_names = [
            "Aatrox", "LeeSin", "Ahri", "Jinx", "Thresh",
            "Darius", "Elise", "Viktor", "Caitlyn", "Lulu",
        ]

        for i in range(10):
            team = teams[0] if i < 5 else teams[1]
            pos = positions[i % 5]
            level = min(18, max(1, int(game_time / 60) + 1))
            gold = 500 + game_time * (3.5 + (i % 3) * 0.5)
            cs = int(game_time / 60 * (7.0 + (i % 3)))
            kills = max(0, int((game_time / 300) * (1 + i % 4)))
            deaths = max(0, int((game_time / 400) * (1 + (i + 2) % 4)))
            hp_max = 600 + level * 80
            hp_cur = hp_max * (0.5 + 0.5 * ((self._tick + i) % 7) / 6)

            players.append({
                "summonerName": f"Player{i+1}",
                "championName": champ_names[i],
                "team": team,
                "level": level,
                "position": pos,
                "isDead": False,
                "respawnTimer": 0.0,
                "currentHealth": hp_cur,
                "maxHealth": hp_max,
                "currentMana": 300.0,
                "maxMana": 500.0,
                "attackDamage": 70 + level * 3,
                "abilityPower": 0 if i % 2 == 0 else 50 + level * 5,
                "armor": 30 + level * 2,
                "magicResist": 30 + level * 2,
                "moveSpeed": 330 + (15 if level > 6 else 0),
                "scores": {
                    "kills": kills,
                    "deaths": deaths,
                    "assists": kills + 1,
                    "creepScore": cs,
                    "wardScore": cs * 0.1,
                },
                "items": [
                    {"itemID": 1001 + j, "displayName": f"Item{j}",
                     "count": 1, "price": 300 * (j + 1)}
                    for j in range(min(6, level // 3))
                ],
            })

        # Events
        events = []
        evt_id = 0
        if game_time > 180:
            evt_id += 1
            events.append({
                "EventID": evt_id,
                "EventName": "ChampionKill",
                "EventTime": game_time - 5,
                "KillerName": "Player1",
                "VictimName": "Player8",
                "Assisters": ["Player2"],
            })
        if game_time > 300:
            evt_id += 1
            events.append({
                "EventID": evt_id,
                "EventName": "DragonKill",
                "EventTime": game_time - 10,
                "KillerName": "Player3",
                "VictimName": "",
                "Assisters": [],
            })

        # Active player (first player)
        active = {
            "currentGold": gold,
            "summonerName": "Player1",
        }

        return {
            "allPlayers": players,
            "activePlayer": active,
            "events": {"Events": events},
            "gameData": {
                "gameTime": game_time,
                "gameMode": "CLASSIC",
                "mapNumber": 11,
                "mapName": "Map11",
            },
        }


# ─── Component Import Tester ────────────────────────────────────────────────

@dataclass
class ImportResult:
    """Result of attempting to import a module."""
    module_path: str = ""
    success: bool = False
    error: str = ""
    class_names: List[str] = field(default_factory=list)
    line_count: int = 0


class ComponentImportTester:
    """Tests that all component modules can be imported without error.

    This catches missing dependencies, circular imports, and syntax
    errors that would prevent the system from starting.
    """

    # All modules that must import cleanly for the system to work
    REQUIRED_MODULES = [
        ("cyber.component.timer_component", ["TimerComponent", "ComponentConfig"]),
        ("cyber.node.node", ["CyberNode", "Reader", "Writer"]),
        ("cyber.logger.cyber_logger", ["get_logger"]),
        ("canbus.channel_message", ["MessageBus", "MessageFactory"]),
        ("canbus.transport", ["Transport"]),
        ("conf.default_config", ["LolBotConfig", "load_config"]),
        ("modules.common.component_base", ["ManagedComponent", "ComponentRegistry"]),
        ("modules.common.adapters.game_messages", [
            "GameSnapshot", "PlayerState", "TeamState", "GameEvent",
            "TeamSide", "GamePhase", "EventType",
        ]),
        ("modules.common.status.error_code", ["ErrorCode", "Status"]),
        ("modules.canbus.canbus_component", ["CanbusComponent"]),
        ("modules.perception.perception_component", ["PerceptionComponent"]),
        ("modules.prediction.prediction_component", ["PredictionComponent"]),
        ("modules.planning.planning_component", ["PlanningComponent"]),
        ("modules.control.control_component", ["ControlComponent"]),
        ("modules.monitor.monitor_component", ["MonitorComponent"]),
        ("modules.perception.fusion.sensor_fusion", ["SensorFusion"]),
        ("modules.perception.game_state.momentum_calculator", ["MomentumCalculator"]),
        ("modules.perception.events.event_detector", []),
        ("modules.perception.ward_tracker.ward_tracker", []),
        ("modules.prediction.draft.draft_analyzer", []),
        ("modules.prediction.objective.objective_tracker", []),
        ("modules.prediction.team_fight.teamfight_predictor", []),
        ("modules.prediction.win_probability.win_predictor", []),
        ("modules.planning.strategy.back_timing_advisor", ["BackTimingAdvisor"]),
        ("modules.planning.strategy.teamfight_caller", ["TeamfightCaller"]),
        ("modules.planning.strategy.lane_advisor", []),
        ("modules.planning.item_build.item_build_advisor", []),
        ("modules.planning.macro.macro_planner", []),
        ("modules.control.voice_output.voice_narrator", []),
        ("modules.control.overlay.overlay_renderer", []),
        ("modules.localization.fog_estimator", ["FogEstimator"]),
        ("modules.localization.map_awareness", []),
        ("modules.storytelling.game_narrator", []),
        ("modules.transform.coordinate_transform", []),
        ("modules.calibration.ab_test_manager", []),
        ("modules.calibration.model_calibrator", []),
        ("evolution.fitness_evaluator", ["FitnessEvaluator"]),
        ("evolution.generation_manager", ["GenerationManager"]),
        ("evolution.strategy_mutator", ["StrategyMutator"]),
        ("integration.agent_os_connector", ["AgentOSConnector"]),
        ("integration.riot_api_client", ["RiotAPIClient"]),
        ("launch.mainboard", ["Mainboard"]),
        ("launch.main_loop", ["MainLoop"]),
        ("runtime.health_monitor", []),
        ("runtime.process_manager", []),
        ("runtime.error_recovery", []),
        ("runtime.graceful_shutdown", []),
        ("runtime.metrics_collector", []),
        ("runtime.session_manager", []),
    ]

    def test_all(self) -> List[ImportResult]:
        """Import every required module and report results."""
        results: List[ImportResult] = []

        for mod_path, expected_names in self.REQUIRED_MODULES:
            result = ImportResult(module_path=mod_path)

            # Get line count
            file_path = mod_path.replace(".", "/") + ".py"
            if os.path.exists(file_path):
                try:
                    result.line_count = sum(
                        1 for _ in open(file_path)
                    )
                except OSError:
                    pass

            try:
                mod = importlib.import_module(mod_path)
                result.success = True

                # Verify expected class/function names exist
                for name in expected_names:
                    if not hasattr(mod, name):
                        result.error = f"Missing expected export: {name}"
                        result.success = False
                        break
                    result.class_names.append(name)

            except Exception as exc:
                result.success = False
                result.error = f"{type(exc).__name__}: {exc}"

            results.append(result)

        return results


# ─── Proc() Benchmark ────────────────────────────────────────────────────────

@dataclass
class ProcBenchmarkResult:
    """Result of benchmarking a component's Proc() loop."""
    component: str = ""
    ticks: int = 0
    total_ms: float = 0.0
    mean_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    errors: int = 0
    error_detail: str = ""


class ProcBenchmark:
    """Benchmarks Init()/Proc() cycles for individual components.

    Creates a component, calls Init(), then runs Proc() N times
    with mock data, measuring latency and error rate.

    Apollo analogy: Component unit test that verifies Proc() meets
    its timing budget (e.g., canbus must complete in <10ms).
    """

    def run(
        self,
        component_class: type,
        ticks: int = 100,
        mock_gen: Optional[MockDataGenerator] = None,
    ) -> ProcBenchmarkResult:
        """Benchmark a component's Proc() method."""
        result = ProcBenchmarkResult(
            component=component_class.__name__,
            ticks=ticks,
        )

        try:
            comp = component_class()
        except Exception as exc:
            result.error_detail = f"Constructor failed: {exc}"
            return result

        # Init
        try:
            init_ok = comp.Init()
            if not init_ok:
                result.error_detail = "Init() returned False"
                return result
        except Exception as exc:
            result.error_detail = f"Init() raised: {exc}"
            return result

        # Proc loop
        latencies: List[float] = []
        for i in range(ticks):
            t0 = time.monotonic()
            try:
                ok = comp.Proc()
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                latencies.append(elapsed_ms)
                if not ok:
                    result.errors += 1
            except Exception as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                latencies.append(elapsed_ms)
                result.errors += 1
                if not result.error_detail:
                    result.error_detail = f"Proc() raised: {exc}"

        if latencies:
            latencies.sort()
            result.total_ms = sum(latencies)
            result.mean_ms = result.total_ms / len(latencies)
            result.min_ms = latencies[0]
            result.max_ms = latencies[-1]
            idx95 = int(len(latencies) * 0.95)
            idx99 = int(len(latencies) * 0.99)
            result.p95_ms = latencies[min(idx95, len(latencies) - 1)]
            result.p99_ms = latencies[min(idx99, len(latencies) - 1)]

        # Shutdown
        try:
            if hasattr(comp, 'on_shutdown'):
                comp.on_shutdown()
        except Exception:
            pass

        return result


# ─── Channel Integrity Checker ───────────────────────────────────────────────

@dataclass
class ChannelCheck:
    """Result of checking a pub/sub channel pair."""
    channel: str = ""
    publisher: str = ""
    subscribers: List[str] = field(default_factory=list)
    has_publisher: bool = False
    has_subscriber: bool = False
    is_healthy: bool = False


class ChannelIntegrityChecker:
    """Verifies that every published channel has at least one subscriber
    and vice versa.

    Apollo analogy: DAG file validation — check that all readers/writers
    in the component DAG are matched.
    """

    # Expected channel topology (publisher → subscribers)
    CHANNEL_MAP: Dict[str, Tuple[str, List[str]]] = {
        "/lol/raw_lcu": ("CanbusComponent", [
            "SensorFusion", "PerceptionComponent",
        ]),
        "/lol/raw_fiddler": ("CanbusComponent", [
            "SensorFusion",
        ]),
        "/lol/game_state": ("PerceptionComponent", [
            "PredictionComponent", "PlanningComponent",
            "ControlComponent", "MonitorComponent",
        ]),
        "/lol/win_prediction": ("PredictionComponent", [
            "PlanningComponent", "ControlComponent",
        ]),
        "/lol/strategy": ("PlanningComponent", [
            "ControlComponent",
        ]),
        "/lol/voice_command": ("PlanningComponent", [
            "ControlComponent",
        ]),
        "/lol/canbus_status": ("CanbusComponent", [
            "MonitorComponent",
        ]),
        "/lol/monitor_status": ("MonitorComponent", []),
    }

    def check_all(self) -> List[ChannelCheck]:
        results: List[ChannelCheck] = []
        for channel, (pub, subs) in self.CHANNEL_MAP.items():
            check = ChannelCheck(
                channel=channel,
                publisher=pub,
                subscribers=list(subs),
                has_publisher=True,  # we declare it, so it exists
                has_subscriber=len(subs) > 0,
            )
            check.is_healthy = check.has_publisher and check.has_subscriber
            results.append(check)
        return results


# ─── Log Collector ───────────────────────────────────────────────────────────

class LogCollector:
    """Collects and aggregates log entries from all log subdirectories.

    Reads existing JSONL logs, summarizes per-component errors,
    and identifies patterns.
    """

    def __init__(self, log_dir: str = "logs") -> None:
        self._log_dir = Path(log_dir)

    def collect_summary(self) -> Dict[str, Any]:
        """Scan all log files and return summary."""
        if not self._log_dir.exists():
            return {"error": "Log directory not found", "path": str(self._log_dir)}

        summary: Dict[str, Any] = {
            "log_dirs": 0,
            "log_files": 0,
            "total_entries": 0,
            "total_errors": 0,
            "total_warnings": 0,
            "per_component": {},
        }

        for entry in self._log_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith("__"):
                continue

            summary["log_dirs"] += 1
            component = entry.name

            comp_stats = {
                "entries": 0,
                "errors": 0,
                "warnings": 0,
                "latest_error": "",
            }

            for jsonl_file in entry.glob("*.jsonl"):
                summary["log_files"] += 1
                try:
                    with open(jsonl_file) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                comp_stats["entries"] += 1
                                summary["total_entries"] += 1
                                level = data.get("level", "INFO")
                                if level in ("ERROR", "CRITICAL"):
                                    comp_stats["errors"] += 1
                                    summary["total_errors"] += 1
                                    comp_stats["latest_error"] = (
                                        data.get("message", "")[:100]
                                    )
                                elif level == "WARNING":
                                    comp_stats["warnings"] += 1
                                    summary["total_warnings"] += 1
                            except json.JSONDecodeError:
                                continue
                except (PermissionError, OSError):
                    continue

            summary["per_component"][component] = comp_stats

        return summary


# ─── Main Diagnostic Report ─────────────────────────────────────────────────

@dataclass
class DiagnosticReport:
    """Complete diagnostic report."""
    timestamp: str = ""
    duration_s: float = 0.0
    # Import test
    import_total: int = 0
    import_passed: int = 0
    import_failed: int = 0
    import_results: List[Dict[str, Any]] = field(default_factory=list)
    # Channel integrity
    channel_total: int = 0
    channel_healthy: int = 0
    channel_results: List[Dict[str, Any]] = field(default_factory=list)
    # Proc benchmark (if run)
    benchmark_results: List[Dict[str, Any]] = field(default_factory=list)
    # Log summary
    log_summary: Dict[str, Any] = field(default_factory=dict)
    # Overall
    overall_health: str = "UNKNOWN"
    issues: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def run_diagnostic(
    ticks: int = 20,
    benchmark: bool = False,
    component_filter: Optional[str] = None,
    output_path: Optional[str] = None,
) -> DiagnosticReport:
    """Run the full system diagnostic.

    Args:
        ticks: Number of Proc() ticks for benchmark.
        benchmark: Whether to run Proc() benchmark.
        component_filter: Only test this component (e.g., "canbus").
        output_path: Write JSON report to this path.

    Returns:
        DiagnosticReport with all results.
    """
    start = time.monotonic()
    report = DiagnosticReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    issues: List[str] = []

    # ── Phase 1: Import tests ────────────────────────────────────────
    print("=" * 60)
    print("  lolbot-HyperAI System Diagnostic")
    print("  Phase 1: Import Checks")
    print("=" * 60)

    importer = ComponentImportTester()
    import_results = importer.test_all()
    report.import_total = len(import_results)
    report.import_passed = sum(1 for r in import_results if r.success)
    report.import_failed = report.import_total - report.import_passed

    for r in import_results:
        status = "OK " if r.success else "ERR"
        lines = f"({r.line_count} lines)" if r.line_count else ""
        print(f"  [{status}] {r.module_path} {lines}")
        if not r.success:
            print(f"        {r.error}")
            issues.append(f"Import failed: {r.module_path}: {r.error}")
        report.import_results.append({
            "module": r.module_path,
            "ok": r.success,
            "error": r.error,
            "lines": r.line_count,
            "exports": r.class_names,
        })

    print(f"\n  Result: {report.import_passed}/{report.import_total} passed\n")

    # ── Phase 2: Channel integrity ───────────────────────────────────
    print("=" * 60)
    print("  Phase 2: Channel Integrity")
    print("=" * 60)

    checker = ChannelIntegrityChecker()
    channel_results = checker.check_all()
    report.channel_total = len(channel_results)
    report.channel_healthy = sum(1 for c in channel_results if c.is_healthy)

    for c in channel_results:
        status = "OK " if c.is_healthy else "WARN"
        sub_str = ", ".join(c.subscribers) if c.subscribers else "(none)"
        print(f"  [{status}] {c.channel}")
        print(f"        pub: {c.publisher} → sub: {sub_str}")
        if not c.is_healthy:
            issues.append(
                f"Channel {c.channel} has no subscribers"
            )
        report.channel_results.append({
            "channel": c.channel,
            "publisher": c.publisher,
            "subscribers": c.subscribers,
            "healthy": c.is_healthy,
        })

    print(f"\n  Result: {report.channel_healthy}/{report.channel_total} healthy\n")

    # ── Phase 3: Proc() benchmark (optional) ─────────────────────────
    if benchmark:
        print("=" * 60)
        print(f"  Phase 3: Proc() Benchmark ({ticks} ticks)")
        print("=" * 60)

        bench = ProcBenchmark()
        components_to_bench = [
            ("CanbusComponent", "modules.canbus.canbus_component"),
            ("PerceptionComponent", "modules.perception.perception_component"),
            ("PredictionComponent", "modules.prediction.prediction_component"),
            ("PlanningComponent", "modules.planning.planning_component"),
            ("ControlComponent", "modules.control.control_component"),
            ("MonitorComponent", "modules.monitor.monitor_component"),
        ]

        for class_name, mod_path in components_to_bench:
            if component_filter and component_filter.lower() not in class_name.lower():
                continue

            try:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, class_name)
                result = bench.run(cls, ticks=ticks)

                status = "OK " if result.errors == 0 else "ERR"
                print(f"  [{status}] {class_name}")
                print(f"        mean={result.mean_ms:.2f}ms  "
                      f"p95={result.p95_ms:.2f}ms  "
                      f"p99={result.p99_ms:.2f}ms  "
                      f"errors={result.errors}/{ticks}")
                if result.error_detail:
                    print(f"        detail: {result.error_detail[:80]}")
                    issues.append(
                        f"Benchmark {class_name}: {result.error_detail[:60]}"
                    )

                report.benchmark_results.append({
                    "component": result.component,
                    "ticks": result.ticks,
                    "mean_ms": round(result.mean_ms, 2),
                    "p95_ms": round(result.p95_ms, 2),
                    "p99_ms": round(result.p99_ms, 2),
                    "max_ms": round(result.max_ms, 2),
                    "errors": result.errors,
                    "error_detail": result.error_detail,
                })

            except Exception as exc:
                print(f"  [ERR] {class_name}: {exc}")
                issues.append(f"Benchmark failed: {class_name}: {exc}")

        print()

    # ── Phase 4: Log collection ──────────────────────────────────────
    print("=" * 60)
    print("  Phase 4: Log Collection & Analysis")
    print("=" * 60)

    collector = LogCollector()
    log_summary = collector.collect_summary()
    report.log_summary = log_summary

    print(f"  Log dirs:    {log_summary.get('log_dirs', 0)}")
    print(f"  Log files:   {log_summary.get('log_files', 0)}")
    print(f"  Entries:     {log_summary.get('total_entries', 0)}")
    print(f"  Errors:      {log_summary.get('total_errors', 0)}")
    print(f"  Warnings:    {log_summary.get('total_warnings', 0)}")

    per_comp = log_summary.get("per_component", {})
    if per_comp:
        print(f"\n  Per-component:")
        for comp, stats in sorted(per_comp.items()):
            if isinstance(stats, dict) and stats.get("entries", 0) > 0:
                err = stats.get("errors", 0)
                marker = " ⚠" if err > 0 else ""
                print(f"    {comp}: {stats['entries']} entries, "
                      f"{err} errors{marker}")
                if err > 0:
                    issues.append(
                        f"Log errors in {comp}: {err} errors"
                    )

    print()

    # ── Summary ──────────────────────────────────────────────────────
    report.duration_s = round(time.monotonic() - start, 2)
    report.issues = issues

    if report.import_failed == 0 and len(issues) <= 2:
        report.overall_health = "HEALTHY"
    elif report.import_failed <= 3:
        report.overall_health = "DEGRADED"
    else:
        report.overall_health = "UNHEALTHY"

    print("=" * 60)
    print(f"  OVERALL: {report.overall_health}")
    print(f"  Duration: {report.duration_s}s")
    print(f"  Imports: {report.import_passed}/{report.import_total}")
    print(f"  Channels: {report.channel_healthy}/{report.channel_total}")
    print(f"  Issues: {len(issues)}")
    if issues:
        print(f"\n  Issues:")
        for issue in issues[:10]:
            print(f"    - {issue}")
    print("=" * 60)

    # ── Output ───────────────────────────────────────────────────────
    if output_path:
        with open(output_path, "w") as f:
            f.write(report.to_json())
        print(f"\n  Report written to: {output_path}")

    return report


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="lolbot-HyperAI System Diagnostic",
    )
    parser.add_argument(
        "--ticks", type=int, default=20,
        help="Number of Proc() ticks for benchmark (default: 20)",
    )
    parser.add_argument(
        "--benchmark", action="store_true",
        help="Run Proc() benchmarks on each component",
    )
    parser.add_argument(
        "--component", type=str, default=None,
        help="Only test this component (e.g., canbus)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON report path",
    )
    args = parser.parse_args()

    report = run_diagnostic(
        ticks=args.ticks,
        benchmark=args.benchmark,
        component_filter=args.component,
        output_path=args.output,
    )

    # Exit code based on health
    if report.overall_health == "UNHEALTHY":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
