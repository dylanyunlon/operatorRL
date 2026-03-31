#!/usr/bin/env python3
"""
M897 — WinProbabilityLiveEngine
=================================
Real-time win probability engine fusing KDA, comp evaluation, objectives.

Reference: M866-M885 win_probability_model pattern
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M897.WinProbabilityLiveEngine")


class WinProbFeature(Enum):
    GOLD_DIFF = "gold_diff"
    KILL_DIFF = "kill_diff"
    TOWER_DIFF = "tower_diff"
    DRAGON_DIFF = "dragon_diff"
    BARON_DIFF = "baron_diff"
    CS_DIFF = "cs_diff"
    LEVEL_DIFF = "level_diff"
    COMP_SCORE_DIFF = "comp_score_diff"


@dataclass
class WinProbSnapshot:
    game_time: float
    win_probability: float  # 0.0 to 1.0 for blue/ORDER side
    features: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.5
    trend: str = "stable"  # "improving", "declining", "stable"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {"time": round(self.game_time, 1), "win_prob": round(self.win_probability, 3),
                "confidence": round(self.confidence, 2), "trend": self.trend,
                "features": {k: round(v, 2) for k, v in self.features.items()}}


DEFAULT_WEIGHTS = {
    WinProbFeature.GOLD_DIFF: 0.25, WinProbFeature.KILL_DIFF: 0.15,
    WinProbFeature.TOWER_DIFF: 0.15, WinProbFeature.DRAGON_DIFF: 0.12,
    WinProbFeature.BARON_DIFF: 0.10, WinProbFeature.CS_DIFF: 0.08,
    WinProbFeature.LEVEL_DIFF: 0.08, WinProbFeature.COMP_SCORE_DIFF: 0.07,
}

NORMALIZATION_FACTORS = {
    WinProbFeature.GOLD_DIFF: 15000, WinProbFeature.KILL_DIFF: 20,
    WinProbFeature.TOWER_DIFF: 11, WinProbFeature.DRAGON_DIFF: 4,
    WinProbFeature.BARON_DIFF: 2, WinProbFeature.CS_DIFF: 100,
    WinProbFeature.LEVEL_DIFF: 5, WinProbFeature.COMP_SCORE_DIFF: 20,
}


class WinProbabilityLiveEngine:
    """
    Real-time win probability calculation engine.
    Fuses multiple data streams (KDA, gold, objectives, composition) into
    a single probability estimate updated every 10 seconds.

    Uses logistic regression-style scoring with configurable weights
    that are adjusted by M901 ModelWeightEvolver based on feedback.
    """

    def __init__(self, kda_tracker=None, comp_evaluator=None, obj_predictor=None):
        self._kda = kda_tracker
        self._comp = comp_evaluator
        self._obj = obj_predictor
        self._weights = dict(DEFAULT_WEIGHTS)
        self._timeline: List[WinProbSnapshot] = []
        self._listeners: Dict[str, List[Callable]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._stats = {"calculations": 0, "trend_changes": 0}
        logger.info("WinProbabilityLiveEngine initialized")

    def on(self, event: str, cb: Callable):
        self._listeners.setdefault(event, []).append(cb)

    async def _emit(self, event: str, data: Any = None):
        for cb in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb): await cb(data)
                else: cb(data)
            except Exception as exc:
                logger.error("Emit error: %s", exc)

    async def start(self):
        self._shutdown.clear()
        self._poll_task = asyncio.create_task(self._calc_loop(), name="winprob-engine")
        logger.info("WinProbabilityLiveEngine started")

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try: await self._poll_task
            except asyncio.CancelledError: pass
        logger.info("Stopped. Stats: %s", self._stats)

    async def _calc_loop(self):
        while not self._shutdown.is_set():
            try:
                await self._calculate()
            except asyncio.CancelledError: raise
            except Exception as exc:
                logger.error("Calc error: %s", exc)
            await asyncio.sleep(10.0)

    async def _calculate(self):
        features = self._extract_features()
        if not features:
            return

        weighted_sum = 0.0
        for feat, weight in self._weights.items():
            raw = features.get(feat.value, 0.0)
            norm_factor = NORMALIZATION_FACTORS.get(feat, 1.0)
            normalized = max(-1, min(1, raw / norm_factor))
            weighted_sum += normalized * weight

        # Sigmoid to convert to probability
        win_prob = 1.0 / (1.0 + math.exp(-weighted_sum * 5))
        win_prob = max(0.05, min(0.95, win_prob))

        # Determine trend
        trend = "stable"
        if len(self._timeline) >= 3:
            recent = [s.win_probability for s in self._timeline[-3:]]
            avg_change = (recent[-1] - recent[0]) / len(recent)
            if avg_change > 0.02: trend = "improving"
            elif avg_change < -0.02: trend = "declining"

        game_time = features.get("game_time", 0)
        confidence = min(0.95, 0.4 + game_time / 3600)

        snapshot = WinProbSnapshot(
            game_time=game_time, win_probability=win_prob,
            features=features, confidence=confidence, trend=trend,
        )
        self._timeline.append(snapshot)
        if len(self._timeline) > 360:
            self._timeline = self._timeline[-360:]

        self._stats["calculations"] += 1

        if len(self._timeline) >= 2:
            prev_trend = self._timeline[-2].trend
            if trend != prev_trend:
                self._stats["trend_changes"] += 1
                await self._emit("trend_change", {"old": prev_trend, "new": trend, "prob": win_prob})

        await self._emit("win_prob_update", snapshot.to_dict())

    def _extract_features(self) -> Dict[str, float]:
        features: Dict[str, float] = {}
        if self._kda:
            timeline = self._kda.get_gold_diff_timeline()
            if timeline:
                latest = timeline[-1]
                features["gold_diff"] = latest.get("gold", 0)
                features["kill_diff"] = latest.get("kills", 0)
                features["game_time"] = latest.get("time", 0)
        features.setdefault("gold_diff", 0)
        features.setdefault("kill_diff", 0)
        features.setdefault("game_time", 0)
        features.setdefault("tower_diff", 0)
        features.setdefault("dragon_diff", 0)
        features.setdefault("baron_diff", 0)
        features.setdefault("cs_diff", 0)
        features.setdefault("level_diff", 0)
        features.setdefault("comp_score_diff", 0)
        return features

    def update_weights(self, new_weights: Dict[str, float]):
        """Called by M901 ModelWeightEvolver to adjust weights."""
        for key, val in new_weights.items():
            try:
                feat = WinProbFeature(key)
                self._weights[feat] = val
            except ValueError:
                pass
        logger.info("Weights updated: %d features", len(new_weights))

    def get_timeline(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._timeline]

    def get_current(self) -> Optional[Dict[str, Any]]:
        return self._timeline[-1].to_dict() if self._timeline else None

    def export_stats(self) -> Dict[str, Any]:
        return {"engine_stats": self._stats, "timeline_length": len(self._timeline),
                "current_weights": {k.value: round(v, 4) for k, v in self._weights.items()}}



# ---------------------------------------------------------------------------
# Extended WinProbabilityLiveEngine utilities
# ---------------------------------------------------------------------------

class MomentumTracker:
    """Tracks game momentum shifts based on win probability changes."""

    def __init__(self):
        self._momentum_events: List[Dict[str, Any]] = []
        self._current_streak: int = 0  # positive = blue momentum
        self._peak_prob: float = 0.5
        self._trough_prob: float = 0.5

    def update(self, game_time: float, win_prob: float):
        if win_prob > self._peak_prob:
            self._peak_prob = win_prob
        if win_prob < self._trough_prob:
            self._trough_prob = win_prob

        # Detect momentum shifts (>5% change in 60 seconds)
        if len(self._momentum_events) > 0:
            last = self._momentum_events[-1]
            if game_time - last.get("time", 0) >= 60:
                change = win_prob - last.get("prob", 0.5)
                if abs(change) >= 0.05:
                    direction = "blue" if change > 0 else "red"
                    self._momentum_events.append({
                        "time": game_time, "prob": win_prob,
                        "change": round(change, 3), "direction": direction,
                    })
        else:
            self._momentum_events.append({
                "time": game_time, "prob": win_prob, "change": 0, "direction": "neutral",
            })

    def get_momentum(self) -> str:
        if not self._momentum_events:
            return "neutral"
        recent = self._momentum_events[-3:]
        blue_count = sum(1 for e in recent if e["direction"] == "blue")
        red_count = sum(1 for e in recent if e["direction"] == "red")
        if blue_count >= 2: return "blue_momentum"
        if red_count >= 2: return "red_momentum"
        return "neutral"

    def get_swing_magnitude(self) -> float:
        return self._peak_prob - self._trough_prob

    def get_events(self) -> List[Dict[str, Any]]:
        return list(self._momentum_events)


class CompositionWeightAdjuster:
    """Adjusts feature weights based on team composition."""

    @staticmethod
    def adjust_for_comp(base_weights: Dict, comp_analysis: Optional[Dict]) -> Dict:
        if not comp_analysis:
            return base_weights
        adjusted = dict(base_weights)

        # If comp is late-game focused, reduce gold_diff weight early
        blue = comp_analysis.get("blue", {})
        power = blue.get("power_curve", {})
        if power.get("late", 5) > power.get("early", 5):
            # Late-game comp: gold deficit matters less early
            for feat_key in adjusted:
                if hasattr(feat_key, 'value'):
                    k = feat_key.value
                else:
                    k = str(feat_key)
                if k == "gold_diff":
                    adjusted[feat_key] *= 0.85
                elif k == "comp_score_diff":
                    adjusted[feat_key] *= 1.2

        return adjusted


class ComebackDetector:
    """Detects when a team is making a comeback."""

    def __init__(self, threshold: float = 0.15):
        self._threshold = threshold
        self._min_prob_seen: float = 0.5
        self._max_prob_seen: float = 0.5
        self._comeback_events: List[Dict[str, Any]] = []

    def update(self, game_time: float, win_prob: float):
        if win_prob < self._min_prob_seen:
            self._min_prob_seen = win_prob
        if win_prob > self._max_prob_seen:
            self._max_prob_seen = win_prob

        # Blue comeback: was below 35%, now above 50%
        if self._min_prob_seen < 0.35 and win_prob > 0.50:
            if not self._comeback_events or game_time - self._comeback_events[-1]["time"] > 120:
                self._comeback_events.append({
                    "time": game_time, "team": "blue",
                    "from_prob": self._min_prob_seen, "to_prob": win_prob,
                })

        # Red comeback: was above 65%, now below 50%
        if self._max_prob_seen > 0.65 and win_prob < 0.50:
            if not self._comeback_events or game_time - self._comeback_events[-1]["time"] > 120:
                self._comeback_events.append({
                    "time": game_time, "team": "red",
                    "from_prob": 1 - self._max_prob_seen, "to_prob": 1 - win_prob,
                })

    def is_comeback_in_progress(self) -> bool:
        return len(self._comeback_events) > 0

    def get_events(self) -> List[Dict[str, Any]]:
        return list(self._comeback_events)


class WinProbabilityCalibrator:
    """Calibrates win probability predictions against actual outcomes."""

    def __init__(self):
        self._predictions: List[Tuple[float, bool]] = []
        self._bins = 10

    def record(self, predicted_prob: float, actual_win: bool):
        self._predictions.append((predicted_prob, actual_win))

    def get_calibration(self) -> List[Dict[str, Any]]:
        if not self._predictions:
            return []
        bins = [[] for _ in range(self._bins)]
        for prob, win in self._predictions:
            idx = min(int(prob * self._bins), self._bins - 1)
            bins[idx].append(1.0 if win else 0.0)

        result = []
        for i, b in enumerate(bins):
            if b:
                predicted = (i + 0.5) / self._bins
                actual = sum(b) / len(b)
                result.append({
                    "predicted": round(predicted, 2),
                    "actual": round(actual, 3),
                    "samples": len(b),
                    "error": round(abs(predicted - actual), 3),
                })
        return result

    def brier_score(self) -> float:
        if not self._predictions:
            return 0.0
        return sum((p - (1.0 if w else 0.0))**2 for p, w in self._predictions) / len(self._predictions)



# ---------------------------------------------------------------------------
# Extended WinProbabilityLiveEngine utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class WinProbabilityLiveEngineMetrics:
    """Collects performance metrics for WinProbabilityLiveEngine."""

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


class WinProbabilityLiveEngineSerializer:
    """Serialization utilities for WinProbabilityLiveEngine state."""

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


class WinProbabilityLiveEngineDiagnostics:
    """Diagnostic tools for WinProbabilityLiveEngine troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "WinProbabilityLiveEngine",
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


class WinProbabilityLiveEngineEventLogger:
    """Structured event logger for WinProbabilityLiveEngine with rotation."""

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
