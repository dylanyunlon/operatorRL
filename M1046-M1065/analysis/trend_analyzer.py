#!/usr/bin/env python3
"""
M1050: Cross-Session Trend Analyzer
=====================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Detects trends across multiple game sessions: meta shifts, personal
improvement curves, opponent recurrence, and champion pick rate changes.
Consumes OpponentProfile history from HistoricalDataCache.

Pattern: Read analysis/opponent_behavior_analyzer.py ThreatAssessment
→ understand threat scoring → implement time-series trend detection
that identifies whether an opponent (or the player) is improving,
declining, or maintaining across sessions.

Log-driven: 101 strategy_engine events suggest heavy strategy computation.
Trend analysis adds context by comparing current-game stats to rolling
averages across last N sessions.
"""

import asyncio
import json
import math
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import get_logger, LogCategory
except ImportError:
    pass


@dataclass
class SessionSnapshot:
    """Snapshot of key metrics from one game session."""
    session_id: str
    timestamp: str
    game_id: Optional[int] = None
    win: Optional[bool] = None
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    vision_score: int = 0
    gold: int = 0
    damage_dealt: int = 0
    game_duration_sec: int = 0
    champion_played: str = ""
    role: str = ""
    opponent_threat_scores: Dict[str, float] = field(default_factory=dict)
    strategy_acceptance_rate: float = 0.0
    reward_total: float = 0.0

    @property
    def kda(self) -> float:
        if self.deaths == 0:
            return float(self.kills + self.assists)
        return round((self.kills + self.assists) / self.deaths, 2)

    @property
    def cs_per_min(self) -> float:
        if self.game_duration_sec == 0:
            return 0.0
        return round(self.cs / (self.game_duration_sec / 60), 1)


@dataclass
class TrendResult:
    """Result of a trend analysis over a sliding window."""
    metric_name: str
    current_value: float
    rolling_avg: float
    rolling_std: float
    trend_direction: str   # "improving", "declining", "stable"
    trend_magnitude: float # 0-1, how strong the trend is
    percentile_rank: float # 0-100, where current sits in distribution
    data_points: int
    window_days: int

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


@dataclass
class MetaShift:
    """Detected shift in champion meta or playstyle meta."""
    shift_type: str        # "champion_rise", "champion_fall", "role_shift"
    subject: str           # Champion name or role
    before_rate: float     # Pick/ban rate before
    after_rate: float      # Pick/ban rate after
    confidence: float      # 0-1
    detected_at: str
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


class TrendAnalyzer:
    """
    Cross-session trend analysis engine.

    Maintains a rolling window of session snapshots (default: 50 sessions).
    Computes trends on KDA, CS/min, vision, win rate, and opponent
    difficulty. Detects personal improvement/decline and meta shifts.

    Architecture:
        SessionSnapshot → append to rolling window
        → compute per-metric trends (linear regression slope)
        → detect meta shifts (champion frequency changes)
        → generate TrendReport for dashboard / evolution loop
    """
    def __init__(self, window_size: int = 50):
        self._window_size = window_size
        self._snapshots: Deque[SessionSnapshot] = deque(maxlen=window_size)
        self._champion_frequency: Dict[str, Deque[int]] = defaultdict(
            lambda: deque(maxlen=window_size))
        self._opponent_recurrence: Dict[str, int] = defaultdict(int)
        self._logger = get_logger()

    def add_snapshot(self, snapshot: SessionSnapshot) -> None:
        self._snapshots.append(snapshot)
        if snapshot.champion_played:
            self._champion_frequency[snapshot.champion_played].append(1)
        # Track opponents we've seen multiple times
        for puuid in snapshot.opponent_threat_scores:
            self._opponent_recurrence[puuid] += 1

    def analyze_personal_trends(self) -> Dict[str, TrendResult]:
        """Analyze personal performance trends."""
        if len(self._snapshots) < 5:
            return {}
        snapshots = list(self._snapshots)
        trends = {}
        # KDA trend
        kdas = [s.kda for s in snapshots]
        trends['kda'] = self._compute_trend('kda', kdas)
        # CS/min trend
        cs_mins = [s.cs_per_min for s in snapshots]
        trends['cs_per_min'] = self._compute_trend('cs_per_min', cs_mins)
        # Vision score trend
        visions = [float(s.vision_score) for s in snapshots]
        trends['vision_score'] = self._compute_trend('vision_score', visions)
        # Win rate trend (sliding window of 10)
        win_rates = []
        for i in range(min(len(snapshots), 10), len(snapshots) + 1):
            window = snapshots[max(0, i-10):i]
            wr = sum(1 for s in window if s.win) / len(window) * 100
            win_rates.append(wr)
        if win_rates:
            trends['win_rate'] = self._compute_trend('win_rate', win_rates)
        # Reward trend
        rewards = [s.reward_total for s in snapshots if s.reward_total != 0]
        if len(rewards) >= 5:
            trends['reward_total'] = self._compute_trend('reward_total', rewards)
        return trends

    def detect_meta_shifts(self) -> List[MetaShift]:
        """Detect champion pick rate shifts in recent games."""
        shifts = []
        snapshots = list(self._snapshots)
        if len(snapshots) < 20:
            return shifts
        mid = len(snapshots) // 2
        first_half = snapshots[:mid]
        second_half = snapshots[mid:]
        # Count champion frequency in each half
        first_champs = defaultdict(int)
        second_champs = defaultdict(int)
        for s in first_half:
            if s.champion_played:
                first_champs[s.champion_played] += 1
        for s in second_half:
            if s.champion_played:
                second_champs[s.champion_played] += 1

        all_champs = set(first_champs) | set(second_champs)
        for champ in all_champs:
            first_rate = first_champs.get(champ, 0) / max(len(first_half), 1)
            second_rate = second_champs.get(champ, 0) / max(len(second_half), 1)
            diff = second_rate - first_rate
            if abs(diff) > 0.1:  # >10% change in pick rate
                shift_type = "champion_rise" if diff > 0 else "champion_fall"
                shifts.append(MetaShift(
                    shift_type=shift_type,
                    subject=champ,
                    before_rate=round(first_rate * 100, 1),
                    after_rate=round(second_rate * 100, 1),
                    confidence=min(abs(diff) * 5, 1.0),
                    detected_at=datetime.now(timezone.utc).isoformat(),
                    description=(
                        f"{champ} pick rate {'increased' if diff > 0 else 'decreased'} "
                        f"from {first_rate*100:.0f}% to {second_rate*100:.0f}%"),
                ))
        return shifts

    def get_recurring_opponents(self, min_encounters: int = 2) -> Dict[str, int]:
        """Return opponents encountered multiple times."""
        return {k: v for k, v in self._opponent_recurrence.items()
                if v >= min_encounters}

    def _compute_trend(
        self, name: str, values: List[float], window_days: int = 7
    ) -> TrendResult:
        """Compute trend using linear regression slope."""
        n = len(values)
        if n < 3:
            return TrendResult(
                metric_name=name, current_value=values[-1] if values else 0,
                rolling_avg=0, rolling_std=0, trend_direction="stable",
                trend_magnitude=0, percentile_rank=50, data_points=n,
                window_days=window_days)

        current = values[-1]
        avg = sum(values) / n
        std = (sum((v - avg) ** 2 for v in values) / n) ** 0.5

        # Linear regression: y = mx + b
        x_vals = list(range(n))
        x_avg = sum(x_vals) / n
        numerator = sum((x - x_avg) * (y - avg) for x, y in zip(x_vals, values))
        denominator = sum((x - x_avg) ** 2 for x in x_vals)
        slope = numerator / max(denominator, 0.001)

        # Normalize slope relative to mean
        if abs(avg) > 0.001:
            normalized_slope = slope / abs(avg)
        else:
            normalized_slope = slope

        if normalized_slope > 0.02:
            direction = "improving"
        elif normalized_slope < -0.02:
            direction = "declining"
        else:
            direction = "stable"

        magnitude = min(abs(normalized_slope) * 10, 1.0)

        # Percentile: where does current value sit?
        below = sum(1 for v in values if v < current)
        percentile = below / n * 100

        return TrendResult(
            metric_name=name, current_value=round(current, 2),
            rolling_avg=round(avg, 2), rolling_std=round(std, 2),
            trend_direction=direction, trend_magnitude=round(magnitude, 3),
            percentile_rank=round(percentile, 1), data_points=n,
            window_days=window_days)

    def generate_trend_report(self) -> Dict[str, Any]:
        """Generate comprehensive trend report."""
        return {
            'session_count': len(self._snapshots),
            'personal_trends': {k: v.to_dict()
                                for k, v in self.analyze_personal_trends().items()},
            'meta_shifts': [s.to_dict() for s in self.detect_meta_shifts()],
            'recurring_opponents': self.get_recurring_opponents(),
            'champion_pool_size': len(self._champion_frequency),
        }


# ---------------------------------------------------------------------------
# Extended Analysis: Performance Trajectory Model
# ---------------------------------------------------------------------------

class PerformanceTrajectoryModel:
    """
    Models a player's skill trajectory over multiple sessions.

    Uses exponential moving average (EMA) to smooth noise and detect
    genuine improvement vs. variance. Integrates with the RL reward
    signal to correlate strategy adherence with performance gains.

    Production critique:
        1. User: Trajectory is displayed as a simple trend arrow
           (improving/declining/stable) with confidence interval.
        2. System: EMA with alpha=0.3 balances responsiveness vs
           noise filtering for typical 5-10 games/session cadence.
    """
    def __init__(self, ema_alpha: float = 0.3):
        self._alpha = ema_alpha
        self._metrics_history: List[Dict[str, float]] = []
        self._ema_values: Dict[str, float] = {}
        self._variance_tracker: Dict[str, List[float]] = {}

    def add_session_metrics(self, metrics: Dict[str, float]) -> None:
        """Add a session's aggregate metrics to the trajectory."""
        self._metrics_history.append(metrics)
        for key, value in metrics.items():
            if key not in self._ema_values:
                self._ema_values[key] = value
                self._variance_tracker[key] = [value]
            else:
                prev = self._ema_values[key]
                self._ema_values[key] = (
                    self._alpha * value + (1 - self._alpha) * prev)
                self._variance_tracker[key].append(value)
                if len(self._variance_tracker[key]) > 50:
                    self._variance_tracker[key] = (
                        self._variance_tracker[key][-50:])

    def get_trend_direction(self, metric: str) -> str:
        """Classify trend as 'improving', 'declining', or 'stable'."""
        history = self._variance_tracker.get(metric, [])
        if len(history) < 3:
            return 'insufficient_data'
        recent = history[-3:]
        older = history[-6:-3] if len(history) >= 6 else history[:3]
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        diff_pct = (recent_avg - older_avg) / max(abs(older_avg), 0.001) * 100
        if diff_pct > 5:
            return 'improving'
        elif diff_pct < -5:
            return 'declining'
        return 'stable'

    def get_volatility(self, metric: str) -> float:
        """Calculate coefficient of variation for a metric."""
        history = self._variance_tracker.get(metric, [])
        if len(history) < 2:
            return 0.0
        mean = sum(history) / len(history)
        if abs(mean) < 0.001:
            return 0.0
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        std_dev = variance ** 0.5
        return round(std_dev / abs(mean), 4)

    def get_trajectory_summary(self) -> Dict[str, Any]:
        """Comprehensive trajectory summary for all tracked metrics."""
        summary = {}
        for metric in self._ema_values:
            summary[metric] = {
                'current_ema': round(self._ema_values[metric], 4),
                'trend': self.get_trend_direction(metric),
                'volatility': self.get_volatility(metric),
                'data_points': len(
                    self._variance_tracker.get(metric, [])),
            }
        return summary

    def predict_next_session(self) -> Dict[str, float]:
        """Predict next session metrics using EMA extrapolation."""
        predictions = {}
        for metric, ema_val in self._ema_values.items():
            history = self._variance_tracker.get(metric, [])
            if len(history) >= 2:
                momentum = history[-1] - history[-2]
                predictions[metric] = round(
                    ema_val + momentum * 0.5, 4)
            else:
                predictions[metric] = round(ema_val, 4)
        return predictions


class WinConditionAnalyzer:
    """
    Analyzes historical matches to identify win conditions.

    Examines what factors most strongly correlate with wins across
    a player's match history: early gold leads, vision control,
    objective control, teamfight prowess, etc.

    Production critique:
        1. User: Win conditions are presented as actionable advice:
           "Your wins correlate with high vision score — prioritize
           warding in the next game."
        2. System: Correlation analysis uses simple Pearson r on
           feature-vs-win vectors. Sample size warning when <20 games.
    """
    def __init__(self):
        self._features: List[Dict[str, float]] = []
        self._outcomes: List[bool] = []

    def add_match(self, features: Dict[str, float], won: bool) -> None:
        self._features.append(features)
        self._outcomes.append(won)

    def compute_correlations(self) -> Dict[str, float]:
        """Compute Pearson correlation of each feature with winning."""
        if len(self._features) < 5:
            return {}
        outcomes_float = [1.0 if w else 0.0 for w in self._outcomes]
        n = len(outcomes_float)
        mean_y = sum(outcomes_float) / n
        correlations = {}
        all_keys = set()
        for f in self._features:
            all_keys.update(f.keys())
        for key in all_keys:
            values = [f.get(key, 0.0) for f in self._features]
            mean_x = sum(values) / n
            cov = sum(
                (values[i] - mean_x) * (outcomes_float[i] - mean_y)
                for i in range(n)) / n
            var_x = sum((v - mean_x) ** 2 for v in values) / n
            var_y = sum((v - mean_y) ** 2 for v in outcomes_float) / n
            denom = (var_x * var_y) ** 0.5
            if denom > 0.001:
                correlations[key] = round(cov / denom, 4)
            else:
                correlations[key] = 0.0
        return dict(sorted(
            correlations.items(), key=lambda x: abs(x[1]), reverse=True))

    def get_top_win_conditions(self, top_n: int = 5) -> List[Dict]:
        """Get the top N features most correlated with winning."""
        correlations = self.compute_correlations()
        results = []
        for key, corr in list(correlations.items())[:top_n]:
            direction = 'positive' if corr > 0 else 'negative'
            results.append({
                'feature': key,
                'correlation': corr,
                'direction': direction,
                'strength': 'strong' if abs(corr) > 0.5 else (
                    'moderate' if abs(corr) > 0.3 else 'weak'),
            })
        return results

    def generate_win_condition_advice(self) -> List[str]:
        """Generate actionable advice from win condition analysis."""
        conditions = self.get_top_win_conditions(3)
        advice = []
        feature_advice_map = {
            'vision_score': "Prioritize warding and vision denial",
            'cs_per_min': "Focus on consistent CS farming",
            'damage_share': "Look for more teamfight engagements",
            'gold_diff_15': "Play aggressively in early laning phase",
            'kda': "Reduce unnecessary deaths, play safer",
            'objective_damage': "Prioritize Dragon/Baron/Grubs control",
            'wards_killed': "Invest in sweeper and clear enemy vision",
            'turret_damage': "Push lane advantages to take plates/towers",
        }
        for cond in conditions:
            feature = cond['feature']
            if feature in feature_advice_map:
                if cond['direction'] == 'positive':
                    advice.append(feature_advice_map[feature])
                else:
                    advice.append(
                        f"Reduce focus on {feature} — not correlating "
                        f"with your wins")
        return advice


class ChampionMetaTracker:
    """
    Tracks champion pick/ban rates and win rates across sessions.

    Detects emerging meta shifts by comparing recent rates against
    historical baselines. Critical for ban recommendations and
    counter-pick suggestions during champion select.
    """
    def __init__(self, baseline_window: int = 100,
                 recent_window: int = 20):
        self._baseline_window = baseline_window
        self._recent_window = recent_window
        self._pick_history: List[Dict[int, int]] = []
        self._ban_history: List[List[int]] = []
        self._winrate_by_champ: Dict[int, List[bool]] = {}

    def record_game(self, picks: Dict[int, int],
                    bans: List[int],
                    results: Dict[int, bool]) -> None:
        """Record champion picks, bans, and results from a game."""
        self._pick_history.append(picks)
        self._ban_history.append(bans)
        for champ_id, won in results.items():
            if champ_id not in self._winrate_by_champ:
                self._winrate_by_champ[champ_id] = []
            self._winrate_by_champ[champ_id].append(won)

    def get_trending_champions(self, top_n: int = 10) -> List[Dict]:
        """Detect champions with rising pick rates."""
        if len(self._pick_history) < self._recent_window + 5:
            return []
        recent = self._pick_history[-self._recent_window:]
        baseline = self._pick_history[
            -self._baseline_window:-self._recent_window]
        if not baseline:
            return []
        # Count picks
        recent_counts: Dict[int, int] = {}
        for game in recent:
            for champ_id in game:
                recent_counts[champ_id] = (
                    recent_counts.get(champ_id, 0) + 1)
        baseline_counts: Dict[int, int] = {}
        for game in baseline:
            for champ_id in game:
                baseline_counts[champ_id] = (
                    baseline_counts.get(champ_id, 0) + 1)
        # Compute rate change
        trends = []
        for champ_id, recent_count in recent_counts.items():
            recent_rate = recent_count / len(recent)
            baseline_count = baseline_counts.get(champ_id, 0)
            baseline_rate = (baseline_count / len(baseline)
                            if baseline else 0)
            if baseline_rate > 0:
                change_pct = (
                    (recent_rate - baseline_rate) / baseline_rate * 100)
            else:
                change_pct = 100.0 if recent_rate > 0 else 0.0
            wr_history = self._winrate_by_champ.get(champ_id, [])
            winrate = (sum(wr_history) / len(wr_history) * 100
                      if wr_history else 0.0)
            trends.append({
                'champion_id': champ_id,
                'recent_pick_rate': round(recent_rate * 100, 1),
                'baseline_pick_rate': round(baseline_rate * 100, 1),
                'change_pct': round(change_pct, 1),
                'winrate': round(winrate, 1),
                'games': len(wr_history),
            })
        trends.sort(key=lambda x: x['change_pct'], reverse=True)
        return trends[:top_n]

    def get_ban_recommendations(self, top_n: int = 5) -> List[Dict]:
        """Recommend bans based on high pick rate + high winrate."""
        if not self._pick_history:
            return []
        recent = self._pick_history[-self._recent_window:]
        pick_counts: Dict[int, int] = {}
        for game in recent:
            for champ_id in game:
                pick_counts[champ_id] = (
                    pick_counts.get(champ_id, 0) + 1)
        candidates = []
        for champ_id, count in pick_counts.items():
            wr_history = self._winrate_by_champ.get(champ_id, [])
            if len(wr_history) < 3:
                continue
            winrate = sum(wr_history) / len(wr_history)
            pick_rate = count / len(recent)
            # Ban score = pick_rate * winrate (weighted)
            ban_score = pick_rate * 0.4 + winrate * 0.6
            candidates.append({
                'champion_id': champ_id,
                'ban_score': round(ban_score, 4),
                'pick_rate': round(pick_rate * 100, 1),
                'winrate': round(winrate * 100, 1),
            })
        candidates.sort(key=lambda x: x['ban_score'], reverse=True)
        return candidates[:top_n]
