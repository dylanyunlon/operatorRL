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
                            timestamp=data.get("ts", data.get("timestamp", 0.0)),
                            level=data.get("level", data.get("severity", "INFO")),
                            component=data.get("component", component),
                            message=data.get("msg", data.get("message", "")),
                            extra=data,
                        )
                        self._entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except (PermissionError, OSError):
            pass

    def analyze(self) -> AnalysisResult:
        if not self._entries:
            return AnalysisResult()

        result = AnalysisResult(total_entries=len(self._entries))

        error_messages = Counter()
        component_counts = Counter()

        for entry in self._entries:
            component_counts[entry.component] += 1
            if entry.level in ("ERROR", "CRITICAL"):
                result.error_count += 1
                error_messages[entry.message[:80]] += 1
            elif entry.level == "WARNING":
                result.warning_count += 1

        result.top_errors = error_messages.most_common(10)
        result.per_component = dict(component_counts.most_common())
        result.time_range = (
            self._entries[0].timestamp,
            self._entries[-1].timestamp,
        )

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
