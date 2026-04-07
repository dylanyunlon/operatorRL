"""
modules/perception/vision/ward_tracker.py — Vision score and ward state tracker.
==================================================================================
Claude19 · Enriches perception with vision awareness data

Tracks vision score progression, ward placement/expiry estimates, and
team-level vision control for map awareness assessment.

Apollo analogy: modules/perception/camera/camera_component.cc tracks
sensor coverage. We track vision (ward) coverage.

File location: lolbot-HyperAI/modules/perception/vision/ward_tracker.py
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_WARD_DURATION_STEALTH = 90.0     # Stealth ward lasts 90-120s
_WARD_DURATION_CONTROL = 999.0    # Control ward persists until killed
_VISION_SAMPLE_INTERVAL_S = 5.0   # Sub-sample rate
_MAX_SAMPLES = 300
_VISION_SCORE_PER_MIN_GOOD = 1.5  # Benchmark for good warding

# Vision control levels
_VISION_DOMINANT_THRESHOLD = 1.3   # Our score / their score
_VISION_LOSING_THRESHOLD = 0.7

from enum import Enum


class VisionControlLevel(Enum):
    """Team-level vision control classification."""
    DOMINANT = "dominant"       # Strong vision advantage
    AHEAD = "ahead"            # Slight vision lead
    EVEN = "even"              # Roughly equal
    BEHIND = "behind"          # Vision deficit
    BLIND = "blind"            # Severe vision deficit


@dataclass
class VisionSample:
    """A snapshot of vision scores at a point in time."""
    game_time: float
    blue_vision_score: float
    red_vision_score: float
    blue_wards_placed: int = 0
    red_wards_placed: int = 0


@dataclass
class VisionReport:
    """Analysis of the current vision state."""
    game_time: float = 0.0
    blue_vision_score: float = 0.0
    red_vision_score: float = 0.0
    vision_diff: float = 0.0
    control_level: VisionControlLevel = VisionControlLevel.EVEN
    blue_vision_per_min: float = 0.0
    red_vision_per_min: float = 0.0
    blue_ward_score: float = 0.0
    red_ward_score: float = 0.0
    vision_trend: float = 0.0  # Positive = blue improving
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time": round(self.game_time, 1),
            "blue_vision_score": round(self.blue_vision_score, 1),
            "red_vision_score": round(self.red_vision_score, 1),
            "vision_diff": round(self.vision_diff, 1),
            "control_level": self.control_level.value,
            "blue_vision_per_min": round(self.blue_vision_per_min, 2),
            "red_vision_per_min": round(self.red_vision_per_min, 2),
            "vision_trend": round(self.vision_trend, 3),
            "recommendation": self.recommendation,
        }


class WardTracker:
    """Tracks vision score progression and team vision control.

    Usage::
        tracker = WardTracker()
        # In perception Proc():
        tracker.update(players, game_time)
        report = tracker.analyze(active_team="BLUE")
    """

    def __init__(self) -> None:
        self._samples: Deque[VisionSample] = deque(maxlen=_MAX_SAMPLES)
        self._last_sample_time: float = 0.0
        self._analysis_count: int = 0
        self._last_blue_total: float = 0.0
        self._last_red_total: float = 0.0

    def update(self, players: List[Any], game_time: float) -> None:
        """Record vision scores from all players.

        Sub-sampled to avoid excessive memory.
        """
        if game_time - self._last_sample_time < _VISION_SAMPLE_INTERVAL_S:
            return

        blue_vs = 0.0
        red_vs = 0.0
        blue_wards = 0
        red_wards = 0

        for player in players:
            team_raw = getattr(player, "team", None)
            scores = getattr(player, "scores", None)
            if scores is None:
                continue

            ward_score = getattr(scores, "ward_score", 0.0)

            is_blue = False
            if hasattr(team_raw, "name"):
                is_blue = "BLUE" in team_raw.name.upper() or "ORDER" in team_raw.name.upper()
            elif hasattr(team_raw, "value"):
                is_blue = "ORDER" in str(team_raw.value).upper() or "BLUE" in str(team_raw.value).upper()

            if is_blue:
                blue_vs += ward_score
            else:
                red_vs += ward_score

        self._last_blue_total = blue_vs
        self._last_red_total = red_vs
        self._samples.append(VisionSample(
            game_time=game_time,
            blue_vision_score=blue_vs,
            red_vision_score=red_vs,
        ))
        self._last_sample_time = game_time

    def analyze(self, active_team: str = "BLUE") -> VisionReport:
        """Compute vision control analysis.

        Returns:
            VisionReport with control level, trends, and recommendations.
        """
        self._analysis_count += 1

        if not self._samples:
            return VisionReport()

        latest = self._samples[-1]
        game_time = latest.game_time
        minutes = max(game_time / 60.0, 0.1)

        blue_vpm = latest.blue_vision_score / minutes
        red_vpm = latest.red_vision_score / minutes

        # Vision diff
        vision_diff = latest.blue_vision_score - latest.red_vision_score

        # Control level
        if latest.red_vision_score > 0:
            ratio = latest.blue_vision_score / max(latest.red_vision_score, 0.1)
        else:
            ratio = 2.0 if latest.blue_vision_score > 0 else 1.0

        if active_team == "BLUE":
            if ratio >= _VISION_DOMINANT_THRESHOLD:
                control = VisionControlLevel.DOMINANT
            elif ratio >= 1.05:
                control = VisionControlLevel.AHEAD
            elif ratio >= _VISION_LOSING_THRESHOLD:
                control = VisionControlLevel.EVEN
            elif ratio >= 0.5:
                control = VisionControlLevel.BEHIND
            else:
                control = VisionControlLevel.BLIND
        else:
            inv_ratio = 1.0 / max(ratio, 0.01)
            if inv_ratio >= _VISION_DOMINANT_THRESHOLD:
                control = VisionControlLevel.DOMINANT
            elif inv_ratio >= 1.05:
                control = VisionControlLevel.AHEAD
            elif inv_ratio >= _VISION_LOSING_THRESHOLD:
                control = VisionControlLevel.EVEN
            elif inv_ratio >= 0.5:
                control = VisionControlLevel.BEHIND
            else:
                control = VisionControlLevel.BLIND

        # Trend: compare last 60s window
        trend = self._compute_trend(60.0)

        # Recommendation
        recommendation = self._generate_recommendation(
            control, active_team, blue_vpm, red_vpm,
        )

        return VisionReport(
            game_time=game_time,
            blue_vision_score=latest.blue_vision_score,
            red_vision_score=latest.red_vision_score,
            vision_diff=vision_diff,
            control_level=control,
            blue_vision_per_min=blue_vpm,
            red_vision_per_min=red_vpm,
            blue_ward_score=latest.blue_vision_score,
            red_ward_score=latest.red_vision_score,
            vision_trend=trend,
            recommendation=recommendation,
        )

    def _compute_trend(self, window_s: float) -> float:
        """Compute vision diff trend over the last window_s seconds."""
        if len(self._samples) < 2:
            return 0.0

        latest = self._samples[-1]
        cutoff = latest.game_time - window_s

        old_sample = None
        for s in self._samples:
            if s.game_time >= cutoff:
                old_sample = s
                break

        if old_sample is None or old_sample is latest:
            return 0.0

        old_diff = old_sample.blue_vision_score - old_sample.red_vision_score
        new_diff = latest.blue_vision_score - latest.red_vision_score
        elapsed = latest.game_time - old_sample.game_time
        if elapsed < 1.0:
            return 0.0

        return (new_diff - old_diff) / elapsed

    def _generate_recommendation(
        self,
        control: VisionControlLevel,
        active_team: str,
        blue_vpm: float,
        red_vpm: float,
    ) -> str:
        """Generate a ward-related recommendation."""
        our_vpm = blue_vpm if active_team == "BLUE" else red_vpm

        if control == VisionControlLevel.BLIND:
            return "Critical vision deficit — buy control wards immediately"
        elif control == VisionControlLevel.BEHIND:
            return "Behind on vision — prioritize ward placement before objectives"
        elif control == VisionControlLevel.EVEN:
            if our_vpm < _VISION_SCORE_PER_MIN_GOOD:
                return "Vision score below benchmark — ward more consistently"
            return "Vision roughly even — maintain current warding"
        elif control == VisionControlLevel.AHEAD:
            return "Vision advantage — use it to set up aggressive plays"
        elif control == VisionControlLevel.DOMINANT:
            return "Vision dominance — look for picks in enemy jungle"
        return ""

    def stats(self) -> Dict[str, Any]:
        return {
            "analysis_count": self._analysis_count,
            "sample_count": len(self._samples),
            "last_blue_total": round(self._last_blue_total, 1),
            "last_red_total": round(self._last_red_total, 1),
        }

    def reset(self) -> None:
        self._samples.clear()
        self._last_sample_time = 0.0
        self._analysis_count = 0
