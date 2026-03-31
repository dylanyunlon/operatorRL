#!/usr/bin/env python3
"""
M899 — PostGameAnalysisGenerator
==================================
Generates detailed post-game analysis reports with key decision review.

Reference: tools.py game data analysis
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M899.PostGameAnalysisGenerator")


@dataclass
class DecisionPoint:
    game_time: float
    event_type: str
    ai_recommendation: str
    player_action: str
    outcome: str
    was_correct: bool
    impact_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"time": round(self.game_time, 1), "event": self.event_type,
                "ai_said": self.ai_recommendation, "player_did": self.player_action,
                "outcome": self.outcome, "correct": self.was_correct,
                "impact": round(self.impact_score, 2)}


@dataclass
class PostGameReport:
    game_id: str
    result: str  # "win" or "loss"
    duration_seconds: float
    final_win_prob: float
    kda_summary: Dict[str, Any] = field(default_factory=dict)
    gold_diff_final: float = 0
    decision_points: List[DecisionPoint] = field(default_factory=list)
    ai_adoption_rate: float = 0.0
    key_moments: List[Dict[str, Any]] = field(default_factory=list)
    improvement_suggestions: List[str] = field(default_factory=list)
    overall_grade: str = "B"
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {"game_id": self.game_id, "result": self.result,
                "duration": f"{self.duration_seconds/60:.1f}min",
                "win_prob_final": round(self.final_win_prob, 2),
                "kda": self.kda_summary, "gold_diff": round(self.gold_diff_final),
                "decisions": [d.to_dict() for d in self.decision_points],
                "adoption_rate": round(self.ai_adoption_rate, 1),
                "key_moments": self.key_moments,
                "suggestions": self.improvement_suggestions,
                "grade": self.overall_grade}


class PostGameAnalysisGenerator:
    """Generates comprehensive post-game analysis reports."""

    def __init__(self, kda_tracker=None, win_engine=None, voice_advisor=None):
        self._kda = kda_tracker
        self._win = win_engine
        self._voice = voice_advisor
        self._reports: List[PostGameReport] = []
        self._stats = {"reports_generated": 0}
        logger.info("PostGameAnalysisGenerator initialized")

    async def generate_report(self, game_id: str, result: str) -> PostGameReport:
        duration = 0
        kda_summary = {}
        gold_diff = 0
        final_prob = 0.5

        if self._kda:
            timeline = self._kda.get_gold_diff_timeline()
            if timeline:
                gold_diff = timeline[-1].get("gold", 0)
                duration = timeline[-1].get("time", 0)
            standings = self._kda.get_current_standings()
            kda_summary = {"order": len(standings.get("ORDER", [])),
                          "chaos": len(standings.get("CHAOS", []))}

        if self._win:
            win_timeline = self._win.get_timeline()
            if win_timeline:
                final_prob = win_timeline[-1].get("win_prob", 0.5)

        # Analyze decisions
        decisions = self._extract_decision_points()
        adoption = sum(1 for d in decisions if d.was_correct) / max(len(decisions), 1) * 100

        # Grade
        if result == "win" and adoption > 70: grade = "A"
        elif result == "win": grade = "B"
        elif adoption > 60: grade = "C"
        else: grade = "D"

        suggestions = self._generate_suggestions(result, gold_diff, decisions)

        report = PostGameReport(
            game_id=game_id, result=result, duration_seconds=duration,
            final_win_prob=final_prob, kda_summary=kda_summary,
            gold_diff_final=gold_diff, decision_points=decisions,
            ai_adoption_rate=adoption, improvement_suggestions=suggestions,
            overall_grade=grade,
        )
        self._reports.append(report)
        self._stats["reports_generated"] += 1
        logger.info("Generated report for game %s: %s grade=%s", game_id, result, grade)
        return report

    def _extract_decision_points(self) -> List[DecisionPoint]:
        # In production: correlate voice advisor messages with actual game events
        return []

    def _generate_suggestions(self, result: str, gold_diff: float,
                             decisions: List[DecisionPoint]) -> List[str]:
        suggestions = []
        if result == "loss" and gold_diff < -5000:
            suggestions.append("Focus on farming and minimizing deaths in early game")
        if result == "loss":
            suggestions.append("Review objective contest decisions — consider trading instead of contesting from behind")
        suggestions.append("Review decision points where AI recommendation was not followed")
        return suggestions

    def get_reports(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._reports[-20:]]

    def export_stats(self) -> Dict[str, Any]:
        return {"generator_stats": self._stats, "reports_count": len(self._reports)}



# ---------------------------------------------------------------------------
# Extended PostGameAnalysisGenerator utilities
# ---------------------------------------------------------------------------

class PerformanceGrader:
    """Grades individual player performance on multiple dimensions."""

    @staticmethod
    def grade_kda(kills: int, deaths: int, assists: int) -> Tuple[str, float]:
        kda = (kills + assists) / max(deaths, 1)
        if kda >= 5.0: return ("S", kda)
        if kda >= 3.5: return ("A", kda)
        if kda >= 2.5: return ("B", kda)
        if kda >= 1.5: return ("C", kda)
        return ("D", kda)

    @staticmethod
    def grade_cs(cs: int, game_minutes: float) -> Tuple[str, float]:
        cspm = cs / max(game_minutes, 1)
        if cspm >= 9.0: return ("S", cspm)
        if cspm >= 7.5: return ("A", cspm)
        if cspm >= 6.0: return ("B", cspm)
        if cspm >= 4.5: return ("C", cspm)
        return ("D", cspm)

    @staticmethod
    def grade_vision(wards_placed: int, wards_destroyed: int,
                     game_minutes: float) -> Tuple[str, float]:
        score = (wards_placed + wards_destroyed * 1.5) / max(game_minutes, 1)
        if score >= 2.0: return ("S", score)
        if score >= 1.5: return ("A", score)
        if score >= 1.0: return ("B", score)
        if score >= 0.5: return ("C", score)
        return ("D", score)

    @staticmethod
    def overall_grade(grades: List[str]) -> str:
        values = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
        if not grades:
            return "B"
        avg = sum(values.get(g, 3) for g in grades) / len(grades)
        if avg >= 4.5: return "S"
        if avg >= 3.5: return "A"
        if avg >= 2.5: return "B"
        if avg >= 1.5: return "C"
        return "D"


class WinProbabilityReview:
    """Reviews win probability timeline for post-game analysis."""

    @staticmethod
    def find_key_moments(timeline: List[Dict]) -> List[Dict[str, Any]]:
        """Identify moments where win probability changed significantly."""
        moments = []
        for i in range(1, len(timeline)):
            prev = timeline[i-1]
            curr = timeline[i]
            change = curr.get("win_prob", 0.5) - prev.get("win_prob", 0.5)
            if abs(change) >= 0.08:
                moments.append({
                    "time": curr.get("time", 0),
                    "prob_before": round(prev.get("win_prob", 0.5), 3),
                    "prob_after": round(curr.get("win_prob", 0.5), 3),
                    "change": round(change, 3),
                    "direction": "positive" if change > 0 else "negative",
                })
        return moments

    @staticmethod
    def find_turning_point(timeline: List[Dict]) -> Optional[Dict[str, Any]]:
        """Find the single biggest turning point in the game."""
        if not timeline:
            return None
        max_change = 0
        turning_point = None
        for i in range(1, len(timeline)):
            change = abs(timeline[i].get("win_prob", 0.5) - timeline[i-1].get("win_prob", 0.5))
            if change > max_change:
                max_change = change
                turning_point = {
                    "time": timeline[i].get("time", 0),
                    "change": round(change, 3),
                    "prob": round(timeline[i].get("win_prob", 0.5), 3),
                }
        return turning_point


class ObjectiveReview:
    """Reviews objective decisions in post-game."""

    @staticmethod
    def analyze_objective_decisions(history: List[Dict]) -> Dict[str, Any]:
        total = len(history)
        contested = sum(1 for h in history if h.get("contest") != "uncontested")
        won = sum(1 for h in history if h.get("team") == "ORDER")

        return {
            "total_objectives": total,
            "contested": contested,
            "won_by_team": won,
            "contest_rate": round(contested / max(total, 1) * 100, 1),
            "win_rate": round(won / max(total, 1) * 100, 1),
        }


class ImprovementEngine:
    """Generates actionable improvement suggestions from post-game data."""

    @staticmethod
    def generate(report: PostGameReport) -> List[Dict[str, Any]]:
        suggestions = []

        if report.result == "loss":
            if report.gold_diff_final < -8000:
                suggestions.append({
                    "area": "economy",
                    "priority": "high",
                    "suggestion": "Focus on CS fundamentals — aim for 7+ CS/min",
                    "metric": f"Gold deficit: {abs(report.gold_diff_final):.0f}",
                })

            if report.ai_adoption_rate < 50:
                suggestions.append({
                    "area": "decision_making",
                    "priority": "medium",
                    "suggestion": "Try following AI objective recommendations more often",
                    "metric": f"AI adoption rate: {report.ai_adoption_rate:.0f}%",
                })

        if report.overall_grade in ("C", "D"):
            suggestions.append({
                "area": "overall",
                "priority": "high",
                "suggestion": "Review key decision points from this game",
                "metric": f"Grade: {report.overall_grade}",
            })

        suggestions.append({
            "area": "continuous",
            "priority": "low",
            "suggestion": "Watch the replay of the turning point moment",
            "metric": "Always improve",
        })

        return suggestions


class ReportFormatter:
    """Formats post-game reports for different output targets."""

    @staticmethod
    def to_text(report: PostGameReport) -> str:
        lines = [
            f"=== Post-Game Report: {report.game_id} ===",
            f"Result: {report.result.upper()}",
            f"Duration: {report.duration_seconds/60:.1f} minutes",
            f"Final Win Probability: {report.final_win_prob*100:.0f}%",
            f"Gold Differential: {report.gold_diff_final:+.0f}",
            f"AI Adoption Rate: {report.ai_adoption_rate:.0f}%",
            f"Overall Grade: {report.overall_grade}",
            "",
            "--- Improvement Suggestions ---",
        ]
        for s in report.improvement_suggestions:
            lines.append(f"  • {s}")
        return "\n".join(lines)

    @staticmethod
    def to_json(report: PostGameReport) -> str:
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)



# ---------------------------------------------------------------------------
# Extended PostGameAnalysisGenerator utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class PostGameAnalysisGeneratorMetrics:
    """Collects performance metrics for PostGameAnalysisGenerator."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class PostGameAnalysisGeneratorSerializer:
    """Serialization utilities for PostGameAnalysisGenerator state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class PostGameAnalysisGeneratorDiagnostics:
    """Diagnostic tools for PostGameAnalysisGenerator troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "PostGameAnalysisGenerator",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class PostGameAnalysisGeneratorEventLogger:
    """Structured event logger for PostGameAnalysisGenerator with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)



class PostGameAnalysisGeneratorConfigStore:
    """Configuration store for runtime settings."""
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._defaults: Dict[str, Any] = {}
        self._change_log: List[Dict[str, Any]] = []

    def set_default(self, key: str, value: Any):
        self._defaults[key] = value
        if key not in self._config:
            self._config[key] = value

    def get(self, key: str, fallback: Any = None) -> Any:
        return self._config.get(key, self._defaults.get(key, fallback))

    def set(self, key: str, value: Any):
        old = self._config.get(key)
        self._config[key] = value
        self._change_log.append({
            "key": key, "old": old, "new": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def reset_to_defaults(self):
        self._config = dict(self._defaults)

    def get_all(self) -> Dict[str, Any]:
        merged = dict(self._defaults)
        merged.update(self._config)
        return merged

    def get_changes(self) -> List[Dict[str, Any]]:
        return list(self._change_log)


class PostGameAnalysisGeneratorHealthCheck:
    """Periodic health check for the module."""
    def __init__(self, instance):
        self._instance = instance
        self._check_results: List[Dict[str, Any]] = []
        self._consecutive_failures = 0

    def check(self) -> Dict[str, Any]:
        result = {
            "module": "PostGameAnalysisGenerator",
            "healthy": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }
        # Verify instance is responsive
        try:
            if hasattr(self._instance, "export_stats"):
                stats = self._instance.export_stats()
                result["checks"].append({"name": "export_stats", "ok": True})
            self._consecutive_failures = 0
        except Exception as exc:
            result["healthy"] = False
            result["checks"].append({"name": "export_stats", "ok": False, "error": str(exc)})
            self._consecutive_failures += 1

        result["consecutive_failures"] = self._consecutive_failures
        self._check_results.append(result)
        if len(self._check_results) > 100:
            self._check_results = self._check_results[-100:]
        return result

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._check_results)

    @property
    def is_healthy(self) -> bool:
        if not self._check_results:
            return True
        return self._check_results[-1].get("healthy", False)


class PostGameAnalysisGeneratorDataValidator:
    """Validates input and output data for the module."""

    @staticmethod
    def validate_dict(data: Dict[str, Any], required_keys: List[str]) -> Tuple[bool, List[str]]:
        errors = []
        for key in required_keys:
            if key not in data:
                errors.append(f"Missing required key: {key}")
        return len(errors) == 0, errors

    @staticmethod
    def validate_numeric_range(value: float, min_val: float, max_val: float,
                                field_name: str = "value") -> Tuple[bool, str]:
        if value < min_val or value > max_val:
            return False, f"{field_name} {value} outside range [{min_val}, {max_val}]"
        return True, ""

    @staticmethod
    def sanitize_string(s: str, max_length: int = 256) -> str:
        return s[:max_length].strip()
