#!/usr/bin/env python3
"""
M824 - Report Generator
====================================
OperatorRL Historical Battle System - Structured analysis report creation

查看游戏分析报告生成器的实现方式，理解其模式，
特别是多模块数据是如何汇总为结构化报告的。
从模板引擎开始，遵循该模式实现报告生成器，
使系统可以在每局游戏结束后自动生成详细的分析报告。

Core: Post-game analysis report creation, scouting reports, trend reports
"""

import os
import sys
import json
import time
import math
import logging
import hashlib
import statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.integration.report")
logger.setLevel(logging.DEBUG)

# ─── Constants ──────────────────────────────────────────────────────────────

REPORT_VERSION = "1.0"
MAX_REPORT_SECTIONS = 20
MAX_SUGGESTIONS = 10
REPORT_ARCHIVE_MAX = 100

class ReportFormat(Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"
    TEXT = "text"

class ReportType(Enum):
    POST_GAME = "post_game"
    SCOUTING = "scouting"
    TREND = "trend"
    META = "meta"
    FULL = "full"
    COMPARISON = "comparison"

class SuggestionPriority(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

# ─── Data Models ────────────────────────────────────────────────────────────

@dataclass
class ImprovementSuggestion:
    """A specific improvement suggestion for the player."""
    category: str
    message: str
    priority: SuggestionPriority = SuggestionPriority.MEDIUM
    metric_name: Optional[str] = None
    current_value: Optional[float] = None
    target_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category, "message": self.message,
            "priority": self.priority.value,
            "metric": self.metric_name,
            "current": self.current_value,
            "target": self.target_value,
        }

@dataclass
class ReportSection:
    title: str
    content: str
    data: Optional[Dict[str, Any]] = None
    subsections: List["ReportSection"] = field(default_factory=list)
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title, "content": self.content,
            "data": self.data,
            "subsections": [s.to_dict() for s in self.subsections],
        }

    def to_markdown(self, level: int = 2) -> str:
        prefix = "#" * level
        md = f"{prefix} {self.title}\n\n{self.content}\n\n"
        if self.data:
            md += f"```json\n{json.dumps(self.data, indent=2, default=str)}\n```\n\n"
        for sub in self.subsections:
            md += sub.to_markdown(level + 1)
        return md

    def to_html(self, level: int = 2) -> str:
        tag = f"h{min(level, 6)}"
        html = f"<{tag}>{self.title}</{tag}>\n<p>{self.content}</p>\n"
        if self.data:
            html += f"<pre>{json.dumps(self.data, indent=2, default=str)}</pre>\n"
        for sub in self.subsections:
            html += sub.to_html(level + 1)
        return html

@dataclass
class GeneratedReport:
    report_id: str
    report_type: ReportType
    title: str
    summary: str
    sections: List[ReportSection] = field(default_factory=list)
    suggestions: List[ImprovementSuggestion] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)
    generation_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.report_id, "type": self.report_type.value,
            "title": self.title, "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections],
            "suggestions": [s.to_dict() for s in self.suggestions],
            "generated_at": self.generated_at,
            "generation_time_ms": round(self.generation_time_ms, 1),
            "metadata": self.metadata,
        }

    def to_markdown(self) -> str:
        md = f"# {self.title}\n\n"
        md += f"*Generated: {datetime.fromtimestamp(self.generated_at).isoformat()}*\n\n"
        md += f"**Summary:** {self.summary}\n\n"
        md += "---\n\n"
        for section in sorted(self.sections, key=lambda s: s.order):
            md += section.to_markdown()
        if self.suggestions:
            md += "## Improvement Suggestions\n\n"
            for s in self.suggestions:
                md += f"- **[{s.priority.value}]** {s.message}\n"
            md += "\n"
        return md

    def to_html(self) -> str:
        html = f"<html><body>\n<h1>{self.title}</h1>\n"
        html += f"<p><em>Generated: {datetime.fromtimestamp(self.generated_at).isoformat()}</em></p>\n"
        html += f"<p><strong>Summary:</strong> {self.summary}</p>\n<hr/>\n"
        for section in sorted(self.sections, key=lambda s: s.order):
            html += section.to_html()
        html += "</body></html>"
        return html

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


# ─── Report Generator ──────────────────────────────────────────────────────

class ReportGenerator:
    """
    Generates structured analysis reports from various data sources.
    Supports post-game, scouting, trend, and meta reports in multiple formats.
    """

    def __init__(self):
        self._report_counter = 0
        self._report_history: List[GeneratedReport] = []

    def _gen_report_id(self) -> str:
        self._report_counter += 1
        ts = hashlib.md5(f"{time.time()}_{self._report_counter}".encode()).hexdigest()[:8]
        return f"rpt_{ts}"

    def generate_post_game_report(
        self, match_data: Dict[str, Any],
        performance_metrics: Optional[Dict[str, Any]] = None,
        timeline_summary: Optional[Dict[str, Any]] = None,
    ) -> GeneratedReport:
        """Generate a post-game analysis report."""
        start = time.time()
        match_id = match_data.get("match_id", "unknown")
        win = match_data.get("win", False)
        result_str = "Victory" if win else "Defeat"

        report = GeneratedReport(
            report_id=self._gen_report_id(),
            report_type=ReportType.POST_GAME,
            title=f"Post-Game Analysis: {match_id}",
            summary=f"{result_str} - {match_data.get('champion_name', 'Unknown')} "
                    f"({match_data.get('role', 'Unknown')})",
        )

        overview = ReportSection(
            title="Match Overview", order=1,
            content=(f"Result: {result_str}\n"
                     f"Duration: {match_data.get('duration_min', 0):.1f} minutes\n"
                     f"KDA: {match_data.get('kills', 0)}/{match_data.get('deaths', 0)}/{match_data.get('assists', 0)}"),
            data={"match_id": match_id, "result": result_str},
        )
        report.sections.append(overview)

        if performance_metrics:
            perf = ReportSection(
                title="Performance Analysis", order=2,
                content=(f"Overall Score: {performance_metrics.get('overall_score', 0):.2f}\n"
                         f"Rating: {performance_metrics.get('rating', 'N/A')}"),
                data=performance_metrics,
            )
            report.sections.append(perf)

        if timeline_summary:
            tl = ReportSection(
                title="Key Moments", order=3,
                content=(f"Team Fights: {timeline_summary.get('team_fights', 0)}\n"
                         f"Turning Points: {timeline_summary.get('turning_points', 0)}"),
                data=timeline_summary,
            )
            report.sections.append(tl)

        report.suggestions = self._generate_improvement_suggestions(match_data, performance_metrics)
        report.generation_time_ms = (time.time() - start) * 1000
        self._archive_report(report)
        return report

    def generate_scouting_report(self, scouting_data: Dict[str, Any]) -> GeneratedReport:
        """Generate a pre-game scouting report."""
        start = time.time()
        enemies = scouting_data.get("enemies", [])
        report = GeneratedReport(
            report_id=self._gen_report_id(),
            report_type=ReportType.SCOUTING,
            title="Pre-Game Scouting Report",
            summary=f"Analysis of {len(enemies)} opponents",
        )
        for i, enemy in enumerate(enemies):
            weaknesses = enemy.get("weaknesses", [])
            weakness_str = ", ".join(w.get("category", "") for w in weaknesses) if weaknesses else "None detected"
            section = ReportSection(
                title=f"Opponent: {enemy.get('name', f'Enemy {i+1}')}", order=i + 1,
                content=(f"Rank: {enemy.get('rank', 'Unknown')}\n"
                         f"Threat Level: {enemy.get('threat', 0.5):.0%}\n"
                         f"Recent WR: {enemy.get('winrate', 0.5):.0%}\n"
                         f"Weaknesses: {weakness_str}"),
                data=enemy,
            )
            report.sections.append(section)

        strategy = scouting_data.get("strategy", [])
        if strategy:
            report.sections.append(ReportSection(
                title="Strategic Recommendations", order=len(enemies) + 1,
                content="\n".join(f"- {s}" for s in strategy),
            ))
        report.generation_time_ms = (time.time() - start) * 1000
        self._archive_report(report)
        return report

    def generate_trend_report(self, player_name: str, trend_data: Dict[str, Any]) -> GeneratedReport:
        """Generate a performance trend report."""
        start = time.time()
        report = GeneratedReport(
            report_id=self._gen_report_id(),
            report_type=ReportType.TREND,
            title=f"Performance Trend Report: {player_name}",
            summary=f"Analysis of {trend_data.get('games_analyzed', 0)} recent games",
        )
        for metric_name, trend_info in trend_data.get("trends", {}).items():
            section = ReportSection(
                title=f"Trend: {metric_name}", order=len(report.sections) + 1,
                content=(f"Direction: {trend_info.get('direction', 'stable')}\n"
                         f"Mean: {trend_info.get('mean', 0):.2f}\n"
                         f"Recent: {trend_info.get('recent_5_avg', 0):.2f}"),
                data=trend_info,
            )
            report.sections.append(section)
        report.generation_time_ms = (time.time() - start) * 1000
        self._archive_report(report)
        return report

    def _generate_improvement_suggestions(
        self, match_data: Dict[str, Any],
        metrics: Optional[Dict[str, Any]],
    ) -> List[ImprovementSuggestion]:
        suggestions = []
        deaths = match_data.get("deaths", 0)
        if deaths > 5:
            suggestions.append(ImprovementSuggestion(
                category="Survivability", priority=SuggestionPriority.HIGH,
                message=f"High death count ({deaths}). Focus on positioning and map awareness.",
                metric_name="deaths", current_value=deaths, target_value=3.0,
            ))
        cs = match_data.get("cs_per_min", 0)
        if cs < 6.0 and match_data.get("role") in ("MID", "ADC", "TOP"):
            suggestions.append(ImprovementSuggestion(
                category="Farming", priority=SuggestionPriority.MEDIUM,
                message=f"CS/min ({cs:.1f}) below target. Practice last-hitting.",
                metric_name="cs_per_min", current_value=cs, target_value=7.0,
            ))
        vision = match_data.get("vision_score", 0)
        duration = match_data.get("duration_min", 30)
        if duration > 0 and vision / max(duration, 1) < 0.8:
            suggestions.append(ImprovementSuggestion(
                category="Vision", priority=SuggestionPriority.MEDIUM,
                message="Low vision score. Place more wards and buy control wards.",
                metric_name="vision_score_per_min",
                current_value=round(vision / max(duration, 1), 2), target_value=1.0,
            ))
        if not match_data.get("win") and match_data.get("kills", 0) > match_data.get("deaths", 0):
            suggestions.append(ImprovementSuggestion(
                category="Macro", priority=SuggestionPriority.HIGH,
                message="Good KDA but still lost. Focus on translating leads into objectives.",
            ))
        return suggestions[:MAX_SUGGESTIONS]

    def export_report(self, report: GeneratedReport, fmt: ReportFormat = ReportFormat.MARKDOWN) -> str:
        if fmt == ReportFormat.MARKDOWN:
            return report.to_markdown()
        elif fmt == ReportFormat.JSON:
            return report.to_json()
        elif fmt == ReportFormat.HTML:
            return report.to_html()
        elif fmt == ReportFormat.TEXT:
            return f"{report.title}\n{'=' * len(report.title)}\n\n{report.summary}"
        return report.to_json()

    def save_report(self, report: GeneratedReport, directory: str, fmt: ReportFormat = ReportFormat.MARKDOWN) -> str:
        """Save report to file."""
        ext_map = {ReportFormat.MARKDOWN: ".md", ReportFormat.JSON: ".json",
                   ReportFormat.HTML: ".html", ReportFormat.TEXT: ".txt"}
        ext = ext_map.get(fmt, ".json")
        filename = f"{report.report_id}{ext}"
        filepath = Path(directory) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        content = self.export_report(report, fmt)
        filepath.write_text(content, encoding="utf-8")
        return str(filepath)

    def _archive_report(self, report: GeneratedReport) -> None:
        self._report_history.append(report)
        if len(self._report_history) > REPORT_ARCHIVE_MAX:
            self._report_history = self._report_history[-REPORT_ARCHIVE_MAX:]

    def get_report_history(self, report_type: Optional[ReportType] = None) -> List[Dict[str, Any]]:
        reports = self._report_history
        if report_type:
            reports = [r for r in reports if r.report_type == report_type]
        return [{"id": r.report_id, "type": r.report_type.value,
                 "title": r.title, "generated_at": r.generated_at} for r in reports]




class ReportTemplateEngine:
    """Manages report templates for consistent formatting."""

    def __init__(self):
        self._templates: Dict[ReportType, Dict[str, Any]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self._templates[ReportType.POST_GAME] = {
            "sections": ["overview", "performance", "timeline", "suggestions"],
            "max_sections": 10,
            "include_data": True,
        }
        self._templates[ReportType.SCOUTING] = {
            "sections": ["enemy_profiles", "strategy", "bans"],
            "max_sections": 8,
            "include_data": True,
        }
        self._templates[ReportType.TREND] = {
            "sections": ["overview", "metric_trends", "highlights"],
            "max_sections": 15,
            "include_data": True,
        }

    def get_template(self, report_type: ReportType) -> Dict[str, Any]:
        return self._templates.get(report_type, {"sections": [], "max_sections": 10})

    def register_template(self, report_type: ReportType, template: Dict[str, Any]) -> None:
        self._templates[report_type] = template


class ReportComparer:
    """Compares two reports to identify differences."""

    def compare(self, report_a: GeneratedReport, report_b: GeneratedReport) -> Dict[str, Any]:
        return {
            "report_a": report_a.report_id,
            "report_b": report_b.report_id,
            "type_match": report_a.report_type == report_b.report_type,
            "section_count_diff": len(report_a.sections) - len(report_b.sections),
            "suggestion_count_diff": len(report_a.suggestions) - len(report_b.suggestions),
            "time_diff_seconds": abs(report_a.generated_at - report_b.generated_at),
        }


class ReportAggregator:
    """Aggregates multiple reports into a summary."""

    def aggregate(self, reports: List[GeneratedReport]) -> Dict[str, Any]:
        if not reports:
            return {"count": 0}
        types = defaultdict(int)
        total_sections = 0
        total_suggestions = 0
        for r in reports:
            types[r.report_type.value] += 1
            total_sections += len(r.sections)
            total_suggestions += len(r.suggestions)
        return {
            "count": len(reports),
            "types": dict(types),
            "avg_sections": round(total_sections / len(reports), 1),
            "avg_suggestions": round(total_suggestions / len(reports), 1),
            "date_range": {
                "earliest": min(r.generated_at for r in reports),
                "latest": max(r.generated_at for r in reports),
            },
        }


class BatchReportGenerator:
    """Generates reports for multiple matches in batch."""

    def __init__(self, generator: Optional[ReportGenerator] = None):
        self._generator = generator or ReportGenerator()

    def generate_batch(self, match_data_list: List[Dict[str, Any]]) -> List[GeneratedReport]:
        reports = []
        for match_data in match_data_list:
            report = self._generator.generate_post_game_report(match_data)
            reports.append(report)
        return reports

    def generate_summary(self, reports: List[GeneratedReport]) -> Dict[str, Any]:
        if not reports:
            return {"count": 0}
        wins = sum(1 for r in reports if "Victory" in r.summary)
        return {
            "total_reports": len(reports),
            "wins": wins, "losses": len(reports) - wins,
            "winrate": round(wins / len(reports), 4),
            "avg_generation_ms": round(statistics.mean(
                [r.generation_time_ms for r in reports]
            ), 2) if reports else 0,
        }



# ─── Module Self-Test ─────────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M824_report_generator", "tests": []}

    try:
        gen = ReportGenerator()
        report = gen.generate_post_game_report(
            {"match_id": "test1", "win": True, "champion_name": "Ahri", "role": "MID",
             "duration_min": 28.5, "kills": 10, "deaths": 3, "assists": 8, "cs_per_min": 7.2,
             "vision_score": 25},
            {"overall_score": 0.75, "rating": "A"},
            {"team_fights": 4, "turning_points": 3},
        )
        assert len(report.sections) >= 3
        md = gen.export_report(report)
        assert "Post-Game Analysis" in md
        results["tests"].append({"name": "post_game_report", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "post_game_report", "status": "fail", "error": str(e)})

    try:
        gen = ReportGenerator()
        report = gen.generate_scouting_report({
            "enemies": [
                {"name": "Player1", "rank": "Diamond II", "threat": 0.8, "winrate": 0.55},
                {"name": "Player2", "rank": "Gold I", "threat": 0.4, "winrate": 0.48},
            ],
            "strategy": ["Target Player2", "Ban Player1's main"],
        })
        assert report.report_type == ReportType.SCOUTING
        assert len(report.sections) >= 2
        results["tests"].append({"name": "scouting_report", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "scouting_report", "status": "fail", "error": str(e)})

    try:
        gen = ReportGenerator()
        report = gen.generate_post_game_report(
            {"match_id": "t2", "win": False, "deaths": 8, "kills": 5,
             "role": "ADC", "cs_per_min": 4.5, "vision_score": 10, "duration_min": 25},
        )
        assert len(report.suggestions) > 0
        assert any(s.priority == SuggestionPriority.HIGH for s in report.suggestions)
        results["tests"].append({"name": "improvement_suggestions", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "improvement_suggestions", "status": "fail", "error": str(e)})

    try:
        gen = ReportGenerator()
        r = gen.generate_post_game_report({"match_id": "fmt_test", "win": True})
        md = gen.export_report(r, ReportFormat.MARKDOWN)
        assert "#" in md
        js = gen.export_report(r, ReportFormat.JSON)
        parsed = json.loads(js)
        assert "id" in parsed
        html = gen.export_report(r, ReportFormat.HTML)
        assert "<html>" in html
        results["tests"].append({"name": "export_formats", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "export_formats", "status": "fail", "error": str(e)})

    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results


if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))
