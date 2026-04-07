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


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Production-grade gold trend with prediction, alerts, and export
# ═══════════════════════════════════════════════════════════════════════════

_CROSSOVER_THRESHOLD = 0.0  # Gold diff crosses zero
_WIDENING_RATE = 50.0       # Gold/sec widening → accelerating lead
_NARROWING_RATE = -30.0     # Gold/sec narrowing → comeback in progress


@dataclass
class GoldAlert:
    """Alert generated when gold trend triggers a notable condition."""
    alert_type: str          # "crossover", "widening", "narrowing", "spike"
    description: str
    severity: str            # "info", "warning", "critical"
    game_time: float = 0.0
    gold_diff: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.alert_type,
            "desc": self.description,
            "severity": self.severity,
            "game_time": round(self.game_time, 1),
            "gold_diff": round(self.gold_diff, 0),
        }


@dataclass
class GoldPrediction:
    """Predicted gold diff at a future time point.

    Claude20: Linear extrapolation from current momentum.
    Used by planning to decide if we should fight now or stall.
    """
    predicted_at_game_time: float = 0.0
    predicted_gold_diff: float = 0.0
    confidence: float = 0.0
    methodology: str = "linear_extrapolation"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "at_time": round(self.predicted_at_game_time, 0),
            "gold_diff": round(self.predicted_gold_diff, 0),
            "confidence": round(self.confidence, 3),
            "method": self.methodology,
        }


class GoldTrendAnalyzerV2(GoldTrendAnalyzer):
    """Extended gold trend analyzer with alerts and prediction.

    Claude20: Adds alert generation, future gold prediction, and
    crossover detection on top of the Claude18 base implementation.
    All existing GoldTrendAnalyzer methods preserved.

    Usage::
        analyzer = GoldTrendAnalyzerV2()
        analyzer.record(game_time=500.0, gold_diff=1200.0)
        report = analyzer.analyze()
        alerts = analyzer.check_alerts()
        prediction = analyzer.predict(lookahead_s=120.0)
    """

    def __init__(self, sub_sample_interval_s: float = 1.0) -> None:
        super().__init__(sub_sample_interval_s)
        self._alerts: List[GoldAlert] = []
        self._last_crossover_time: float = 0.0
        self._last_alert_time: float = 0.0
        self._alert_cooldown_s: float = 30.0
        self._prev_gold_diff: float = 0.0
        self._prev_momentum: float = 0.0

    def check_alerts(self) -> List[GoldAlert]:
        """Check for notable gold trend conditions.

        Called after analyze(). Returns new alerts since last check.

        Returns:
            List of GoldAlert objects (may be empty).
        """
        report = self.analyze()
        new_alerts: List[GoldAlert] = []
        gt = report.current_gold_diff
        game_time = self._samples[-1].game_time if self._samples else 0.0

        if game_time - self._last_alert_time < self._alert_cooldown_s:
            return new_alerts

        # Crossover detection: gold lead changed hands
        if (self._prev_gold_diff > 200 and gt < -200) or \
           (self._prev_gold_diff < -200 and gt > 200):
            if game_time - self._last_crossover_time > 60:
                new_team = "Blue" if gt > 0 else "Red"
                alert = GoldAlert(
                    alert_type="crossover",
                    description=f"Gold lead changed to {new_team} ({gt:+.0f}g)",
                    severity="warning",
                    game_time=game_time,
                    gold_diff=gt,
                )
                new_alerts.append(alert)
                self._last_crossover_time = game_time

        # Widening lead detection
        if report.short_momentum > _WIDENING_RATE and abs(gt) > 1000:
            team = "our" if gt > 0 else "enemy"
            alert = GoldAlert(
                alert_type="widening",
                description=f"Gold lead widening ({team} +{report.short_momentum:.0f}g/s)",
                severity="info",
                game_time=game_time,
                gold_diff=gt,
            )
            new_alerts.append(alert)

        # Narrowing lead (comeback detection)
        if report.short_momentum < _NARROWING_RATE and abs(gt) > 1500:
            alert = GoldAlert(
                alert_type="narrowing",
                description=f"Gold lead narrowing ({report.short_momentum:+.0f}g/s)",
                severity="warning",
                game_time=game_time,
                gold_diff=gt,
            )
            new_alerts.append(alert)

        # Spike alert
        if report.recent_spike:
            alert = GoldAlert(
                alert_type="spike",
                description=f"Gold spike: {report.spike_direction} ({report.spike_magnitude:.0f}g)",
                severity="critical" if report.spike_magnitude > 1500 else "warning",
                game_time=game_time,
                gold_diff=gt,
            )
            new_alerts.append(alert)

        if new_alerts:
            self._last_alert_time = game_time
            self._alerts.extend(new_alerts)

        self._prev_gold_diff = gt
        self._prev_momentum = report.short_momentum
        return new_alerts

    def predict(self, lookahead_s: float = 120.0) -> GoldPrediction:
        """Predict gold diff at a future time point.

        Uses linear extrapolation from current short-term momentum.
        Confidence decreases with lookahead distance.

        Args:
            lookahead_s: How far ahead to predict (seconds).

        Returns:
            GoldPrediction with predicted gold diff.
        """
        report = self.analyze()
        if len(self._samples) < 5:
            return GoldPrediction(confidence=0.0)

        current_time = self._samples[-1].game_time
        predicted_diff = report.current_gold_diff + (report.short_momentum * lookahead_s)

        # Confidence decreases with lookahead and volatility
        base_confidence = 0.8
        time_decay = max(0.1, 1.0 - (lookahead_s / 600.0))  # decays over 10min
        volatility_penalty = min(0.5, report.volatility / 200.0)
        confidence = base_confidence * time_decay * (1.0 - volatility_penalty)

        return GoldPrediction(
            predicted_at_game_time=current_time + lookahead_s,
            predicted_gold_diff=predicted_diff,
            confidence=max(0.05, confidence),
            methodology="linear_extrapolation",
        )

    def get_crossover_history(self) -> List[GoldAlert]:
        """Get all crossover alerts since game start."""
        return [a for a in self._alerts if a.alert_type == "crossover"]

    def get_gold_at_time(self, game_time: float) -> Optional[float]:
        """Get historical gold diff at a specific game time.

        Returns the closest sample's gold diff, or None if no data.
        """
        if not self._samples:
            return None
        best = min(self._samples, key=lambda s: abs(s.game_time - game_time))
        if abs(best.game_time - game_time) > 5.0:
            return None  # No sample close enough
        return best.gold_diff

    def export_time_series(self, resolution_s: float = 10.0) -> List[Dict[str, Any]]:
        """Export gold diff time series for dashboard charting.

        Args:
            resolution_s: Time between exported points.

        Returns:
            List of {game_time, gold_diff} dicts.
        """
        if not self._samples:
            return []

        result: List[Dict[str, Any]] = []
        last_exported = -resolution_s
        for sample in self._samples:
            if sample.game_time - last_exported >= resolution_s:
                result.append({
                    "game_time": round(sample.game_time, 1),
                    "gold_diff": round(sample.gold_diff, 0),
                })
                last_exported = sample.game_time
        return result

    def extended_stats(self) -> Dict[str, Any]:
        """Extended stats including alert history."""
        base = self.stats()
        report = self.analyze()
        base.update({
            "current_gold_diff": round(report.current_gold_diff, 0),
            "short_momentum": round(report.short_momentum, 1),
            "medium_momentum": round(report.medium_momentum, 1),
            "advantage": f"{report.advantage_team}_{report.advantage_strength}",
            "total_alerts": len(self._alerts),
            "crossover_count": len(self.get_crossover_history()),
            "recent_alerts": [a.to_dict() for a in self._alerts[-5:]],
        })
        return base
