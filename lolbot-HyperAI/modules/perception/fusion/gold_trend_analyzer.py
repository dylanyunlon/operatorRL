"""
modules/perception/fusion/gold_trend_analyzer.py — Gold diff trend tracking.
==============================================================================
Claude18 · New sub-analyzer for perception fusion layer

Problem: PredictionFeatures.gold_trend only compares current vs ~2min-ago
snapshot. This misses short-term momentum (e.g. 3 kills in 30s = gold spike)
and long-term trend (steadily losing gold for 5 min).

Solution: Track gold diff as a time series and compute:
    - Short-term momentum (last 30s slope)
    - Medium-term trend (last 2min slope)
    - Volatility (how much gold diff swings)
    - Spike detection (sudden gold swings from kills/objectives)

File location: lolbot-HyperAI/modules/perception/fusion/gold_trend_analyzer.py
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_SAMPLES = 600  # 10 minutes at 1 sample/sec
_SHORT_WINDOW_S = 30.0
_MEDIUM_WINDOW_S = 120.0
_SPIKE_THRESHOLD = 500.0  # 500 gold swing in one sample = spike


@dataclass
class GoldSample:
    """Single gold diff measurement."""
    game_time: float
    gold_diff: float  # blue - red


@dataclass
class GoldTrendReport:
    """Analysis of gold diff trends and momentum."""
    current_gold_diff: float = 0.0
    short_momentum: float = 0.0      # Gold/sec over last 30s
    medium_momentum: float = 0.0     # Gold/sec over last 2min
    volatility: float = 0.0          # Std dev of gold diff changes
    recent_spike: bool = False       # True if large sudden swing
    spike_direction: str = "none"    # "blue_gain", "red_gain", "none"
    spike_magnitude: float = 0.0     # Size of spike in gold
    advantage_team: str = "even"     # "blue", "red", "even"
    advantage_strength: str = "none" # "slight", "moderate", "large", "massive"
    sample_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gold_diff": round(self.current_gold_diff, 0),
            "short_momentum": round(self.short_momentum, 1),
            "medium_momentum": round(self.medium_momentum, 1),
            "volatility": round(self.volatility, 1),
            "spike": self.recent_spike,
            "advantage": f"{self.advantage_team}_{self.advantage_strength}",
        }


class GoldTrendAnalyzer:
    """Tracks gold diff as a time series and computes momentum/trends.

    Designed to be called every perception tick (~10Hz). Internally
    sub-samples at ~1Hz to avoid memory bloat.

    Usage::
        analyzer = GoldTrendAnalyzer()
        # In perception Proc():
        analyzer.record(game_time=500.0, gold_diff=1200.0)
        report = analyzer.analyze()
        print(f"Momentum: {report.short_momentum:.1f} gold/s")
    """

    def __init__(self, sub_sample_interval_s: float = 1.0) -> None:
        self._samples: Deque[GoldSample] = deque(maxlen=_MAX_SAMPLES)
        self._sub_sample_interval = sub_sample_interval_s
        self._last_sample_time: float = 0.0
        self._last_gold_diff: float = 0.0
        self._analysis_count: int = 0

    def record(self, game_time: float, gold_diff: float) -> None:
        """Record a gold diff measurement. Sub-sampled to ~1Hz."""
        if game_time - self._last_sample_time < self._sub_sample_interval:
            self._last_gold_diff = gold_diff
            return

        self._samples.append(GoldSample(
            game_time=game_time, gold_diff=gold_diff,
        ))
        self._last_sample_time = game_time
        self._last_gold_diff = gold_diff

    def analyze(self) -> GoldTrendReport:
        """Compute trend analysis from recorded samples."""
        self._analysis_count += 1
        report = GoldTrendReport(
            current_gold_diff=self._last_gold_diff,
            sample_count=len(self._samples),
        )

        if len(self._samples) < 3:
            return report

        # Current time reference
        latest = self._samples[-1]
        report.current_gold_diff = latest.gold_diff

        # Short-term momentum (last 30s linear slope)
        short_samples = self._get_window(_SHORT_WINDOW_S)
        if len(short_samples) >= 2:
            report.short_momentum = self._compute_slope(short_samples)

        # Medium-term momentum (last 2min)
        medium_samples = self._get_window(_MEDIUM_WINDOW_S)
        if len(medium_samples) >= 2:
            report.medium_momentum = self._compute_slope(medium_samples)

        # Volatility (standard deviation of gold_diff deltas)
        if len(self._samples) >= 5:
            deltas = []
            samples_list = list(self._samples)
            for i in range(1, min(30, len(samples_list))):
                deltas.append(
                    samples_list[-i].gold_diff - samples_list[-i-1].gold_diff
                    if i < len(samples_list) else 0.0
                )
            if deltas:
                mean_delta = sum(deltas) / len(deltas)
                variance = sum((d - mean_delta) ** 2 for d in deltas) / len(deltas)
                report.volatility = math.sqrt(variance)

        # Spike detection (last sample vs previous)
        if len(self._samples) >= 2:
            prev = self._samples[-2]
            diff = latest.gold_diff - prev.gold_diff
            if abs(diff) >= _SPIKE_THRESHOLD:
                report.recent_spike = True
                report.spike_magnitude = abs(diff)
                report.spike_direction = (
                    "blue_gain" if diff > 0 else "red_gain"
                )

        # Advantage classification
        gd = report.current_gold_diff
        if abs(gd) < 500:
            report.advantage_team = "even"
            report.advantage_strength = "none"
        else:
            report.advantage_team = "blue" if gd > 0 else "red"
            abs_gd = abs(gd)
            if abs_gd < 1500:
                report.advantage_strength = "slight"
            elif abs_gd < 3000:
                report.advantage_strength = "moderate"
            elif abs_gd < 6000:
                report.advantage_strength = "large"
            else:
                report.advantage_strength = "massive"

        return report

    def _get_window(self, window_s: float) -> List[GoldSample]:
        """Get samples within the last window_s seconds."""
        if not self._samples:
            return []
        cutoff = self._samples[-1].game_time - window_s
        return [s for s in self._samples if s.game_time >= cutoff]

    def _compute_slope(self, samples: List[GoldSample]) -> float:
        """Compute linear regression slope (gold/sec)."""
        if len(samples) < 2:
            return 0.0
        n = len(samples)
        sum_x = sum(s.game_time for s in samples)
        sum_y = sum(s.gold_diff for s in samples)
        sum_xy = sum(s.game_time * s.gold_diff for s in samples)
        sum_x2 = sum(s.game_time ** 2 for s in samples)
        denom = n * sum_x2 - sum_x ** 2
        if abs(denom) < 1e-10:
            return 0.0
        return (n * sum_xy - sum_x * sum_y) / denom

    def reset(self) -> None:
        self._samples.clear()
        self._last_sample_time = 0.0
        self._last_gold_diff = 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "analysis_count": self._analysis_count,
            "sample_count": len(self._samples),
            "time_span_s": (
                round(self._samples[-1].game_time - self._samples[0].game_time, 1)
                if len(self._samples) >= 2 else 0.0
            ),
        }
