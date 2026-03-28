"""
Performance Report - Generates post-game performance reports.

Aggregates all game session data into a comprehensive report
with grades, charts data, and improvement recommendations.
Based on the leagueoflegends-optimizer performance methodology.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.models.game_snapshot import GameSnapshot
from lol_fiddler_agent.feedback.tracker import ComplianceRecord

logger = logging.getLogger(__name__)


@dataclass
class PerformanceGrade:
    """Grade for a specific performance category."""
    category: str
    grade: str  # S, A, B, C, D
    score: float  # 0.0 to 1.0
    details: str = ""
    benchmarks: dict[str, float] = field(default_factory=dict)


@dataclass
class PerformanceReport:
    """Comprehensive post-game performance report."""
    # Metadata
    report_id: str = ""
    champion: str = ""
    game_duration_minutes: float = 0.0
    won: Optional[bool] = None
    generated_at: float = field(default_factory=time.time)

    # Overall
    overall_grade: str = "C"
    overall_score: float = 0.5

    # Category grades
    grades: list[PerformanceGrade] = field(default_factory=list)

    # Key metrics
    kda: float = 0.0
    cs_per_min: float = 0.0
    gold_per_min: float = 0.0
    kill_participation: float = 0.0
    vision_score: float = 0.0
    damage_share: float = 0.0

    # Improvement recommendations
    recommendations: list[str] = field(default_factory=list)

    # Advice compliance
    advice_compliance_rate: float = 0.0
    advice_effectiveness_rate: float = 0.0

    def to_json(self) -> str:
        from lol_fiddler_agent.utils.serialization import to_json
        return to_json(self.__dict__, pretty=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "champion": self.champion,
            "duration_min": self.game_duration_minutes,
            "won": self.won,
            "overall_grade": self.overall_grade,
            "overall_score": self.overall_score,
            "kda": self.kda,
            "cs_per_min": self.cs_per_min,
            "gold_per_min": self.gold_per_min,
            "grades": [
                {"category": g.category, "grade": g.grade, "score": g.score, "details": g.details}
                for g in self.grades
            ],
            "recommendations": self.recommendations,
        }


class ReportGenerator:
    """Generates post-game performance reports.

    Example::

        generator = ReportGenerator()
        report = generator.generate(
            snapshots=game_snapshots,
            compliance_records=feedback_records,
            won=True,
        )
    """

    # Thresholds from lol-optimizer dataset
    _THRESHOLDS = {
        "kda": {"S": 5.0, "A": 3.5, "B": 2.5, "C": 1.5},
        "cs_per_min": {"S": 8.5, "A": 7.0, "B": 5.5, "C": 4.0},
        "deaths_per_min": {"S": 0.10, "A": 0.15, "B": 0.20, "C": 0.30},
    }

    def generate(
        self,
        snapshots: list[GameSnapshot],
        compliance_records: Optional[list[ComplianceRecord]] = None,
        won: Optional[bool] = None,
    ) -> PerformanceReport:
        """Generate a performance report from game data."""
        if not snapshots:
            return PerformanceReport()

        final = snapshots[-1]
        first = snapshots[0]
        duration = final.game_time / 60

        report = PerformanceReport(
            report_id=f"report_{int(time.time())}",
            champion=final.my_champion,
            game_duration_minutes=duration,
            won=won,
        )

        # Extract final stats
        my_player = None
        for p in final.players:
            if p.champion_name == final.my_champion:
                my_player = p
                break

        if my_player:
            report.kda = my_player.kda
            report.cs_per_min = my_player.creep_score / max(duration, 1)

        report.gold_per_min = (
            (final.ally_team.total_gold_estimate if final.ally_team else 0) / 5
        ) / max(duration, 1)

        # Generate grades
        report.grades = self._compute_grades(report, final)

        # Overall score
        if report.grades:
            scores = [g.score for g in report.grades]
            report.overall_score = sum(scores) / len(scores)
            report.overall_grade = self._score_to_grade(report.overall_score)

        # Compliance stats
        if compliance_records:
            followed = sum(1 for r in compliance_records if r.followed)
            positive = sum(1 for r in compliance_records if r.outcome == "positive")
            total = len(compliance_records)
            report.advice_compliance_rate = followed / max(total, 1)
            report.advice_effectiveness_rate = positive / max(total, 1)

        # Recommendations
        report.recommendations = self._generate_recommendations(report, final)

        return report

    def _compute_grades(
        self, report: PerformanceReport, final: GameSnapshot,
    ) -> list[PerformanceGrade]:
        grades: list[PerformanceGrade] = []

        # KDA grade
        kda_score = self._threshold_score(report.kda, self._THRESHOLDS["kda"])
        grades.append(PerformanceGrade(
            category="KDA",
            grade=self._score_to_grade(kda_score),
            score=kda_score,
            details=f"KDA: {report.kda:.2f}",
            benchmarks=self._THRESHOLDS["kda"],
        ))

        # CS grade
        cs_score = self._threshold_score(report.cs_per_min, self._THRESHOLDS["cs_per_min"])
        grades.append(PerformanceGrade(
            category="Farming",
            grade=self._score_to_grade(cs_score),
            score=cs_score,
            details=f"CS/min: {report.cs_per_min:.1f}",
            benchmarks=self._THRESHOLDS["cs_per_min"],
        ))

        # Deaths grade (inverted - fewer deaths = better)
        dpm = final.f1_deaths_per_min
        death_thresholds = self._THRESHOLDS["deaths_per_min"]
        if dpm <= death_thresholds["S"]:
            d_score = 1.0
        elif dpm <= death_thresholds["A"]:
            d_score = 0.75
        elif dpm <= death_thresholds["B"]:
            d_score = 0.5
        elif dpm <= death_thresholds["C"]:
            d_score = 0.25
        else:
            d_score = 0.1

        grades.append(PerformanceGrade(
            category="Survivability",
            grade=self._score_to_grade(d_score),
            score=d_score,
            details=f"Deaths/min: {dpm:.3f}",
        ))

        # Objective impact
        obj_score = 0.5
        if final.ally_team:
            if final.ally_team.dragon_count >= 3:
                obj_score = 0.9
            elif final.ally_team.dragon_count >= 1:
                obj_score = 0.6
        grades.append(PerformanceGrade(
            category="Objective Control",
            grade=self._score_to_grade(obj_score),
            score=obj_score,
            details=f"Dragons: {final.ally_team.dragon_count if final.ally_team else 0}",
        ))

        return grades

    def _generate_recommendations(
        self, report: PerformanceReport, final: GameSnapshot,
    ) -> list[str]:
        recs: list[str] = []

        for grade in report.grades:
            if grade.grade in ("C", "D"):
                if grade.category == "Farming":
                    recs.append(f"CS needs work ({report.cs_per_min:.1f}/min). Practice last-hitting in tool.")
                elif grade.category == "Survivability":
                    recs.append(f"Too many deaths ({final.f1_deaths_per_min:.2f}/min). Focus on positioning.")
                elif grade.category == "KDA":
                    recs.append(f"KDA low ({report.kda:.1f}). Look for safer fights.")

        if report.advice_compliance_rate < 0.4:
            recs.append("Low advice compliance. Try following more recommendations.")

        if not recs:
            recs.append("Strong game overall! Maintain consistency.")

        return recs

    @staticmethod
    def _threshold_score(value: float, thresholds: dict[str, float]) -> float:
        if value >= thresholds["S"]:
            return 1.0
        elif value >= thresholds["A"]:
            return 0.75
        elif value >= thresholds["B"]:
            return 0.5
        elif value >= thresholds["C"]:
            return 0.25
        return 0.1

    @staticmethod
    def _score_to_grade(score: float) -> str:
        if score >= 0.9:
            return "S"
        elif score >= 0.7:
            return "A"
        elif score >= 0.5:
            return "B"
        elif score >= 0.3:
            return "C"
        return "D"
