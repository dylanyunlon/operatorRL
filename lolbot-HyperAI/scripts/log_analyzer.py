"""
LogAnalyzer — JSONL log parsing and Markdown report generation.
================================================================
lolbot-HyperAI · Scripts

Reads all logs/*.jsonl files, parses timestamps and component sources,
generates a Markdown report with latency distributions, error aggregation,
and session timeline.

Usage:
    python -m scripts.log_analyzer [--log-dir logs] [--output report.md]
"""

from __future__ import annotations
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class LogEntry:
    timestamp: float = 0.0
    level: str = "INFO"
    component: str = ""
    message: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    total_entries: int = 0
    error_count: int = 0
    warning_count: int = 0
    top_errors: List[Tuple[str, int]] = field(default_factory=list)
    per_component: Dict[str, int] = field(default_factory=dict)
    time_range: Tuple[float, float] = (0.0, 0.0)
    session_transitions: List[Dict[str, Any]] = field(default_factory=list)
    # Claude15: latency and proc profiling
    latency_by_component: Dict[str, Dict[str, float]] = field(
        default_factory=dict
    )
    proc_overruns: List[Dict[str, Any]] = field(default_factory=list)
    error_timeline: List[Dict[str, Any]] = field(default_factory=list)


class LogAnalyzer:
    """Parses JSONL logs and produces analysis."""

    def __init__(self, log_dir: str = "logs") -> None:
        self._log_dir = Path(log_dir)
        self._entries: List[LogEntry] = []

    def load(self) -> int:
        """Load all JSONL files from log directory. Returns entry count."""
        self._entries.clear()
        if not self._log_dir.exists():
            return 0

        for jsonl_dir in self._log_dir.iterdir():
            if jsonl_dir.is_dir():
                for f in jsonl_dir.glob("*.jsonl"):
                    self._load_file(f)
            elif jsonl_dir.suffix == ".jsonl":
                self._load_file(jsonl_dir)

        self._entries.sort(key=lambda e: e.timestamp)
        return len(self._entries)

    def _load_file(self, path: Path) -> None:
        component = path.stem.replace(".jsonl", "").replace(".", "_")
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = LogEntry(
                            timestamp=self._parse_ts(
                                data.get("ts", data.get("timestamp", 0.0))
                            ),
                            level=data.get("level", data.get("severity", "INFO")),
                            component=data.get("component",
                                              data.get("module", component)),
                            message=data.get("msg", data.get("message", "")),
                            extra=data,
                        )
                        self._entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except (PermissionError, OSError):
            pass

    @staticmethod
    def _parse_ts(raw: Any) -> float:
        """Parse timestamp from either float or ISO 8601 string."""
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            # Handle ISO format: "2026-04-04T13:13:06.376Z"
            try:
                from datetime import datetime, timezone
                raw_clean = raw.rstrip("Z")
                if "T" in raw_clean:
                    dt = datetime.fromisoformat(raw_clean)
                    return dt.replace(tzinfo=timezone.utc).timestamp()
            except (ValueError, ImportError):
                pass
            # Fallback: try float parse
            try:
                return float(raw)
            except ValueError:
                pass
        return 0.0

    def analyze(self) -> AnalysisResult:
        if not self._entries:
            return AnalysisResult()

        result = AnalysisResult(total_entries=len(self._entries))

        error_messages = Counter()
        component_counts = Counter()
        # Claude15: latency tracking per component
        component_latencies: Dict[str, List[float]] = defaultdict(list)

        for entry in self._entries:
            component_counts[entry.component] += 1
            if entry.level in ("ERROR", "CRITICAL"):
                result.error_count += 1
                error_messages[entry.message[:80]] += 1
                result.error_timeline.append({
                    "ts": entry.timestamp,
                    "component": entry.component,
                    "message": entry.message[:100],
                })
            elif entry.level == "WARNING":
                result.warning_count += 1

            # Claude15: extract latency from log entries that contain it
            extra = entry.extra
            latency = extra.get("latency_ms") or extra.get("elapsed_ms")
            if isinstance(latency, (int, float)) and latency > 0:
                component_latencies[entry.component].append(float(latency))

            # Claude15: detect Proc() overruns from warning messages
            if "overrun" in entry.message.lower():
                result.proc_overruns.append({
                    "ts": entry.timestamp,
                    "component": entry.component,
                    "message": entry.message[:120],
                })

        result.top_errors = error_messages.most_common(10)
        result.per_component = dict(component_counts.most_common())
        result.time_range = (
            self._entries[0].timestamp,
            self._entries[-1].timestamp,
        )

        # Claude15: compute latency stats per component
        for comp, lats in component_latencies.items():
            if not lats:
                continue
            lats_sorted = sorted(lats)
            n = len(lats_sorted)
            result.latency_by_component[comp] = {
                "count": n,
                "mean_ms": round(sum(lats) / n, 2),
                "min_ms": round(lats_sorted[0], 2),
                "max_ms": round(lats_sorted[-1], 2),
                "p95_ms": round(
                    lats_sorted[min(int(n * 0.95), n - 1)], 2
                ),
                "p99_ms": round(
                    lats_sorted[min(int(n * 0.99), n - 1)], 2
                ),
            }

        return result

    def generate_report(self, result: Optional[AnalysisResult] = None) -> str:
        if result is None:
            result = self.analyze()

        lines = [
            "# lolbot-HyperAI Log Analysis Report",
            "",
            f"Total entries: {result.total_entries}",
            f"Errors: {result.error_count}",
            f"Warnings: {result.warning_count}",
            "",
            "## Per-Component Message Counts",
            "",
        ]

        for comp, count in sorted(result.per_component.items(),
                                    key=lambda x: -x[1]):
            lines.append(f"- {comp}: {count}")

        lines.extend(["", "## Top Errors", ""])
        if result.top_errors:
            for msg, count in result.top_errors:
                lines.append(f"- [{count}x] {msg}")
        else:
            lines.append("- No errors found")

        # Claude15: latency report
        if result.latency_by_component:
            lines.extend(["", "## Component Latency (Proc() timing)", ""])
            for comp, stats in sorted(result.latency_by_component.items()):
                lines.append(
                    f"- {comp}: mean={stats['mean_ms']}ms "
                    f"p95={stats['p95_ms']}ms "
                    f"p99={stats['p99_ms']}ms "
                    f"max={stats['max_ms']}ms "
                    f"(n={stats['count']})"
                )

        # Claude15: proc overrun report
        if result.proc_overruns:
            lines.extend(["", "## Proc() Overruns", ""])
            for overrun in result.proc_overruns[:20]:
                lines.append(
                    f"- [{overrun['component']}] {overrun['message']}"
                )

        # Claude15: error timeline
        if result.error_timeline:
            lines.extend(["", "## Error Timeline", ""])
            for err in result.error_timeline[:20]:
                lines.append(
                    f"- [{err['component']}] {err['message']}"
                )

        return "\n".join(lines)


def main() -> None:
    log_dir = sys.argv[1] if len(sys.argv) > 1 else "logs"
    analyzer = LogAnalyzer(log_dir)
    count = analyzer.load()
    print(f"Loaded {count} log entries from {log_dir}")
    result = analyzer.analyze()
    report = analyzer.generate_report(result)
    print(report)
    output_path = "log_report.md"
    if len(sys.argv) > 2:
        output_path = sys.argv[2]
    with open(output_path, "w") as f:
        f.write(report)
    print(f"Report written to {output_path}")


if __name__ == "__main__":
    main()
