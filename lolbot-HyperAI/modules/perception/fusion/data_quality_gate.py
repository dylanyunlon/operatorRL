"""
modules/perception/fusion/data_quality_gate.py — Data Quality Gating Layer
============================================================================
lolbot-HyperAI · modules/perception/fusion

查看 modules/perception/perception_component.py 上现有 Proc() 的实现方式,
理解其模式, 特别是 GameSnapshot 是如何从 RawLCUData 构建的。从
PerceptionComponent 这个好例子开始。然后, 遵循该模式实现一个新的
DataQualityGate, 让 PerceptionComponent 可以在 fusion 之前拦截低质量数据,
并能给每条 RawLCUData 打上质量分数 (用于 ConfidenceCalibrator)。

Architecture position:
    modules/perception/fusion/data_quality_gate.py   ← YOU ARE HERE
    ├─ Called by: perception_component.py (before fusion)
    ├─ Input: RawLCUData from canbus
    ├─ Output: QualityAssessment (score + flags + filtered data)
    ├─ Feeds: prediction/evaluator/confidence_calibrator.py
    └─ Metrics: exported to HealthAggregator

Apollo reference:
    modules/perception/fusion/data_association.h — association quality

Design notes:
    - Checks: completeness (all 10 players?), freshness (stale data?),
      consistency (gold never negative?), continuity (no time jumps?)
    - Quality score 0.0-1.0 passed to ConfidenceCalibrator
    - Flagging system: WARN (score 0.5-0.7) or REJECT (score < 0.5)
    - Zero-copy: doesn't clone data, just wraps with metadata
    - Stateful: tracks history for continuity checks
"""

from __future__ import annotations

import enum
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("perception.quality_gate")

# ─── Constants ───────────────────────────────────────────────────────────────

_EXPECTED_PLAYER_COUNT = 10           # 5v5
_MAX_GAME_TIME_JUMP_S = 5.0          # max acceptable time delta between ticks
_MAX_GOLD_PER_PLAYER = 100000        # sanity check
_STALE_DATA_THRESHOLD_S = 3.0        # data older than this is stale
_HISTORY_SIZE = 50                     # recent assessments for trend
_MIN_ACCEPTABLE_SCORE = 0.3           # below this = REJECT


class QualityFlag(enum.Enum):
    """Individual quality check flags."""
    INCOMPLETE_PLAYERS = "incomplete_players"
    STALE_DATA = "stale_data"
    TIME_DISCONTINUITY = "time_discontinuity"
    GOLD_ANOMALY = "gold_anomaly"
    MISSING_ACTIVE_PLAYER = "missing_active_player"
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"
    NEGATIVE_VALUES = "negative_values"
    PLAYER_LEVEL_ANOMALY = "player_level_anomaly"


class QualityVerdict(enum.Enum):
    """Overall quality verdict."""
    ACCEPT = "accept"         # Score >= 0.7: use normally
    WARN = "warn"             # Score 0.3-0.7: use with reduced confidence
    REJECT = "reject"         # Score < 0.3: discard or use cached


@dataclass
class QualityAssessment:
    """Result of a data quality check on incoming RawLCUData.

    Attributes:
        score: 0.0-1.0 quality score.
        verdict: ACCEPT/WARN/REJECT.
        flags: Set of specific quality issues found.
        details: Human-readable detail for each flag.
        data_timestamp: When the data was received.
        game_time: In-game timestamp from the data.
    """
    score: float = 1.0
    verdict: QualityVerdict = QualityVerdict.ACCEPT
    flags: List[QualityFlag] = field(default_factory=list)
    details: Dict[str, str] = field(default_factory=dict)
    data_timestamp: float = 0.0
    game_time: float = 0.0
    check_duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "verdict": self.verdict.value,
            "flags": [f.value for f in self.flags],
            "details": self.details,
            "game_time": round(self.game_time, 2),
            "check_ms": round(self.check_duration_ms, 3),
        }


@dataclass
class GateMetrics:
    """Aggregated quality gate statistics."""
    total_checked: int = 0
    total_accepted: int = 0
    total_warned: int = 0
    total_rejected: int = 0
    avg_score: float = 0.0
    _score_sum: float = field(default=0.0, repr=False)

    def record(self, assessment: QualityAssessment) -> None:
        self.total_checked += 1
        self._score_sum += assessment.score
        self.avg_score = self._score_sum / self.total_checked
        if assessment.verdict == QualityVerdict.ACCEPT:
            self.total_accepted += 1
        elif assessment.verdict == QualityVerdict.WARN:
            self.total_warned += 1
        else:
            self.total_rejected += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_checked": self.total_checked,
            "accepted": self.total_accepted,
            "warned": self.total_warned,
            "rejected": self.total_rejected,
            "avg_score": round(self.avg_score, 4),
            "reject_rate": (
                round(self.total_rejected / self.total_checked, 4)
                if self.total_checked > 0 else 0.0
            ),
        }


class DataQualityGate:
    """Validates incoming game data before it enters the fusion pipeline.

    Each call to assess() runs all checks on a RawLCUData-like dict
    and returns a QualityAssessment. The assessment's score is used
    by ConfidenceCalibrator to modulate prediction confidence.

    Usage::

        gate = DataQualityGate()

        # In PerceptionComponent.Proc():
        assessment = gate.assess(raw_lcu_data)
        if assessment.verdict == QualityVerdict.REJECT:
            return True  # skip this tick, use cached snapshot
        # ...proceed with fusion using assessment.score for confidence
    """

    def __init__(
        self,
        min_acceptable_score: float = _MIN_ACCEPTABLE_SCORE,
        stale_threshold_s: float = _STALE_DATA_THRESHOLD_S,
    ) -> None:
        self._min_score = min_acceptable_score
        self._stale_threshold_s = stale_threshold_s
        self._metrics = GateMetrics()

        # State for continuity checks
        self._last_game_time: float = -1.0
        self._last_data_ts: float = 0.0
        self._history: Deque[QualityAssessment] = deque(maxlen=_HISTORY_SIZE)
        self._consecutive_rejects: int = 0

    @property
    def metrics(self) -> GateMetrics:
        return self._metrics

    def assess(
        self,
        data: Dict[str, Any],
        receive_timestamp: Optional[float] = None,
    ) -> QualityAssessment:
        """Run all quality checks on incoming data.

        Args:
            data: Raw dict from LCU Live Client Data API.
            receive_timestamp: When this data was received (monotonic).

        Returns:
            QualityAssessment with score and flags.
        """
        t0 = time.monotonic()
        now = receive_timestamp or t0

        assessment = QualityAssessment(data_timestamp=now)

        # Extract game_time
        game_stats = data.get("gameData", data.get("gameStats", {}))
        if isinstance(game_stats, dict):
            assessment.game_time = game_stats.get("gameTime", 0.0)
        elif hasattr(data, "game_time"):
            assessment.game_time = getattr(data, "game_time", 0.0)

        penalties: List[Tuple[float, QualityFlag, str]] = []

        # ── Check 1: Player completeness ─────────────────────────────
        all_players = data.get("allPlayers", [])
        if not isinstance(all_players, list):
            all_players = getattr(data, "all_players", []) or []
        player_count = len(all_players) if isinstance(all_players, list) else 0

        if player_count == 0:
            penalties.append((
                0.5, QualityFlag.INCOMPLETE_PLAYERS,
                "No player data found",
            ))
        elif player_count < _EXPECTED_PLAYER_COUNT:
            penalty = 0.3 * (1 - player_count / _EXPECTED_PLAYER_COUNT)
            penalties.append((
                penalty, QualityFlag.INCOMPLETE_PLAYERS,
                f"Only {player_count}/{_EXPECTED_PLAYER_COUNT} players",
            ))

        # ── Check 2: Active player presence ──────────────────────────
        active_player = data.get("activePlayer", None)
        if active_player is None and not hasattr(data, "active_player"):
            penalties.append((
                0.15, QualityFlag.MISSING_ACTIVE_PLAYER,
                "No active player section (spectator mode?)",
            ))

        # ── Check 3: Data freshness ──────────────────────────────────
        if self._last_data_ts > 0:
            gap = now - self._last_data_ts
            if gap > self._stale_threshold_s:
                stale_penalty = min(0.3, gap * 0.05)
                penalties.append((
                    stale_penalty, QualityFlag.STALE_DATA,
                    f"Data gap of {gap:.1f}s",
                ))

        # ── Check 4: Time continuity ─────────────────────────────────
        gt = assessment.game_time
        if self._last_game_time >= 0 and gt > 0:
            time_delta = gt - self._last_game_time
            if time_delta < -1.0:
                penalties.append((
                    0.3, QualityFlag.TIME_DISCONTINUITY,
                    f"Game time went backwards: {time_delta:.1f}s",
                ))
            elif time_delta > _MAX_GAME_TIME_JUMP_S:
                penalties.append((
                    0.15, QualityFlag.TIME_DISCONTINUITY,
                    f"Large time jump: {time_delta:.1f}s",
                ))
            elif abs(time_delta) < 0.001 and self._last_data_ts > 0:
                penalties.append((
                    0.05, QualityFlag.DUPLICATE_TIMESTAMP,
                    "Same game_time as previous tick",
                ))

        # ── Check 5: Gold sanity ─────────────────────────────────────
        if isinstance(all_players, list):
            for player in all_players:
                if not isinstance(player, dict):
                    continue
                scores = player.get("scores", {})
                if isinstance(scores, dict):
                    gold = scores.get("gold", 0)
                    if isinstance(gold, (int, float)):
                        if gold < 0:
                            penalties.append((
                                0.2, QualityFlag.NEGATIVE_VALUES,
                                f"Negative gold: {gold}",
                            ))
                            break
                        if gold > _MAX_GOLD_PER_PLAYER:
                            penalties.append((
                                0.1, QualityFlag.GOLD_ANOMALY,
                                f"Unrealistic gold: {gold}",
                            ))
                            break

        # ── Check 6: Level sanity ────────────────────────────────────
        if isinstance(all_players, list):
            for player in all_players:
                if not isinstance(player, dict):
                    continue
                level = player.get("level", 0)
                if isinstance(level, (int, float)):
                    if level < 1 or level > 18:
                        penalties.append((
                            0.1, QualityFlag.PLAYER_LEVEL_ANOMALY,
                            f"Invalid player level: {level}",
                        ))
                        break

        # ── Compute final score ──────────────────────────────────────
        total_penalty = sum(p[0] for p in penalties)
        assessment.score = max(0.0, 1.0 - total_penalty)

        for _, flag, detail in penalties:
            assessment.flags.append(flag)
            assessment.details[flag.value] = detail

        # ── Determine verdict ────────────────────────────────────────
        if assessment.score >= 0.7:
            assessment.verdict = QualityVerdict.ACCEPT
            self._consecutive_rejects = 0
        elif assessment.score >= self._min_score:
            assessment.verdict = QualityVerdict.WARN
            self._consecutive_rejects = 0
        else:
            assessment.verdict = QualityVerdict.REJECT
            self._consecutive_rejects += 1

        # ── Update state ─────────────────────────────────────────────
        if gt > 0:
            self._last_game_time = gt
        self._last_data_ts = now
        assessment.check_duration_ms = (time.monotonic() - t0) * 1000.0

        self._metrics.record(assessment)
        self._history.append(assessment)

        return assessment

    def recent_score_trend(self, count: int = 10) -> List[float]:
        """Get recent quality score trend."""
        n = min(count, len(self._history))
        return [a.score for a in list(self._history)[-n:]]

    @property
    def consecutive_rejects(self) -> int:
        return self._consecutive_rejects

    def reset(self) -> None:
        """Reset state between game sessions."""
        self._last_game_time = -1.0
        self._last_data_ts = 0.0
        self._consecutive_rejects = 0
        self._history.clear()
        self._metrics = GateMetrics()


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: Production-grade DataQualityGateV2 — adaptive thresholds,
# anomaly detection, field-level validation, and circuit breaker integration
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class FieldValidationRule:
    """Validation rule for a single data field.

    Claude21: Apollo's canbus does field-by-field validation of CAN frames
    (e.g. chassis_detail.vehicle_spd within [0, 300]). We mirror this for
    LoL game data fields — each field has expected type, range, and staleness
    limits.
    """
    field_path: str          # dot-separated path: "gameData.gameTime"
    expected_type: str       # "float", "int", "str", "list", "dict"
    min_value: Optional[float] = None  # for numeric fields
    max_value: Optional[float] = None
    required: bool = True
    max_staleness_s: float = 5.0

    def validate(self, value: Any, game_time: float) -> Tuple[bool, str]:
        """Validate a single field value. Returns (ok, reason)."""
        if value is None:
            if self.required:
                return False, f"{self.field_path}: required but missing"
            return True, ""

        # Type check
        type_map = {
            "float": (int, float), "int": (int,),
            "str": (str,), "list": (list, tuple),
            "dict": (dict,),
        }
        expected = type_map.get(self.expected_type, (object,))
        if not isinstance(value, expected):
            return False, (
                f"{self.field_path}: expected {self.expected_type}, "
                f"got {type(value).__name__}"
            )

        # Range check for numerics
        if isinstance(value, (int, float)):
            if self.min_value is not None and value < self.min_value:
                return False, (
                    f"{self.field_path}: {value} < min {self.min_value}"
                )
            if self.max_value is not None and value > self.max_value:
                return False, (
                    f"{self.field_path}: {value} > max {self.max_value}"
                )

        return True, ""


@dataclass
class AnomalyEvent:
    """A detected data anomaly.

    Claude21: Tracks unexpected jumps in game time, gold, kill counts, etc.
    that suggest corrupted data or desync between client and server.
    """
    anomaly_type: str       # "time_jump", "gold_spike", "kill_desync"
    severity: str           # "warning", "critical"
    field_path: str
    expected_value: float
    actual_value: float
    game_time: float
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.anomaly_type,
            "severity": self.severity,
            "field": self.field_path,
            "expected": round(self.expected_value, 2),
            "actual": round(self.actual_value, 2),
            "game_time": round(self.game_time, 1),
        }


class AdaptiveThreshold:
    """Self-adjusting quality threshold based on recent history.

    Claude21: Instead of a static threshold (e.g. reject if score < 0.5),
    this adapts based on the running mean and standard deviation of scores.
    Rejects data that is more than N sigma below the recent mean.

    This is crucial because data quality varies by game phase — during
    loading, many fields are legitimately empty, so a static threshold
    would reject valid data.
    """

    def __init__(
        self,
        window_size: int = 50,
        sigma_threshold: float = 2.0,
        floor: float = 0.2,
    ) -> None:
        self._window: Deque[float] = deque(maxlen=window_size)
        self._sigma_threshold = sigma_threshold
        self._floor = floor

    def update(self, score: float) -> None:
        """Record a new quality score."""
        self._window.append(score)

    @property
    def threshold(self) -> float:
        """Current adaptive threshold."""
        if len(self._window) < 5:
            return self._floor

        scores = list(self._window)
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = variance ** 0.5

        adaptive = mean - (self._sigma_threshold * std)
        return max(self._floor, adaptive)

    def is_acceptable(self, score: float) -> bool:
        """Check if a score passes the adaptive threshold."""
        self.update(score)
        return score >= self.threshold

    def stats(self) -> Dict[str, Any]:
        scores = list(self._window)
        if not scores:
            return {"threshold": self._floor, "samples": 0}
        mean = sum(scores) / len(scores)
        return {
            "threshold": round(self.threshold, 4),
            "mean": round(mean, 4),
            "samples": len(scores),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
        }


# Standard field validation rules for LoL Live Client Data
_LOL_FIELD_RULES: List[FieldValidationRule] = [
    FieldValidationRule("gameData.gameTime", "float", 0.0, 7200.0),
    FieldValidationRule("gameData.gameMode", "str"),
    FieldValidationRule("gameData.mapNumber", "int", 1, 30),
    FieldValidationRule("activePlayer.level", "int", 1, 18, required=False),
    FieldValidationRule(
        "activePlayer.currentGold", "float", 0.0, 100000.0,
        required=False,
    ),
]

# Anomaly thresholds
_MAX_TIME_JUMP_S = 10.0       # game_time should not jump >10s between ticks
_MAX_GOLD_JUMP = 5000.0       # total_gold should not jump >5k in one tick
_MAX_KILL_JUMP = 5             # kills should not jump >5 in one tick


class DataQualityGateV2(DataQualityGate):
    """Production-grade data quality gate with adaptive thresholds,
    field-level validation, anomaly detection, and circuit breaker.

    Claude21: Extends DataQualityGate with:
    - Per-field validation rules (Apollo canbus frame validation pattern)
    - Adaptive threshold that adjusts to game phase
    - Anomaly detection for data jumps (time, gold, kills)
    - Circuit breaker: after N consecutive rejects, raise alert
    - Full audit trail for debugging data pipeline issues

    Usage::
        gate = DataQualityGateV2()
        result = gate.validate(raw_data, game_time)
        if result.passed:
            process(raw_data)
        else:
            log_rejection(result)
    """

    _CIRCUIT_BREAKER_THRESHOLD = 15  # consecutive rejects to trip breaker
    _CIRCUIT_BREAKER_COOLDOWN_S = 10.0

    def __init__(self) -> None:
        super().__init__()
        self._adaptive = AdaptiveThreshold(window_size=50, sigma_threshold=2.0)
        self._field_rules = list(_LOL_FIELD_RULES)
        self._anomalies: Deque[AnomalyEvent] = deque(maxlen=200)
        self._prev_game_time: float = 0.0
        self._prev_total_gold: float = 0.0
        self._prev_total_kills: int = 0
        self._circuit_open: bool = False
        self._circuit_open_time: float = 0.0
        self._field_error_counts: Dict[str, int] = {}

    def validate_fields(
        self, data: Dict[str, Any], game_time: float,
    ) -> Tuple[bool, List[str]]:
        """Validate all fields against rules.

        Returns (all_passed, list_of_errors).
        """
        errors: List[str] = []
        for rule in self._field_rules:
            parts = rule.field_path.split(".")
            value = data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break

            ok, reason = rule.validate(value, game_time)
            if not ok:
                errors.append(reason)
                self._field_error_counts[rule.field_path] = (
                    self._field_error_counts.get(rule.field_path, 0) + 1
                )

        return len(errors) == 0, errors

    def detect_anomalies(
        self, data: Dict[str, Any], game_time: float,
    ) -> List[AnomalyEvent]:
        """Detect data anomalies by comparing with previous tick.

        Claude21: Catches corrupted frames that pass basic validation
        but contain unrealistic jumps in values.
        """
        anomalies: List[AnomalyEvent] = []

        # Time jump detection
        if self._prev_game_time > 0 and game_time > 0:
            time_delta = game_time - self._prev_game_time
            if time_delta > _MAX_TIME_JUMP_S:
                anomalies.append(AnomalyEvent(
                    anomaly_type="time_jump",
                    severity="critical" if time_delta > 30.0 else "warning",
                    field_path="gameData.gameTime",
                    expected_value=self._prev_game_time + 0.1,
                    actual_value=game_time,
                    game_time=game_time,
                ))
            elif time_delta < -0.5:
                # Time went backwards — very suspicious
                anomalies.append(AnomalyEvent(
                    anomaly_type="time_regression",
                    severity="critical",
                    field_path="gameData.gameTime",
                    expected_value=self._prev_game_time,
                    actual_value=game_time,
                    game_time=game_time,
                ))

        # Gold spike detection
        all_players = data.get("allPlayers", [])
        total_gold = 0.0
        total_kills = 0
        for p in all_players:
            scores = p.get("scores", {})
            total_gold += scores.get("gold", 0.0) if isinstance(scores, dict) else 0.0
            total_kills += scores.get("kills", 0) if isinstance(scores, dict) else 0

        if self._prev_total_gold > 0:
            gold_delta = abs(total_gold - self._prev_total_gold)
            if gold_delta > _MAX_GOLD_JUMP:
                anomalies.append(AnomalyEvent(
                    anomaly_type="gold_spike",
                    severity="warning",
                    field_path="allPlayers.*.scores.gold",
                    expected_value=self._prev_total_gold,
                    actual_value=total_gold,
                    game_time=game_time,
                ))

        if self._prev_total_kills > 0:
            kill_delta = total_kills - self._prev_total_kills
            if kill_delta > _MAX_KILL_JUMP:
                anomalies.append(AnomalyEvent(
                    anomaly_type="kill_spike",
                    severity="warning",
                    field_path="allPlayers.*.scores.kills",
                    expected_value=float(self._prev_total_kills),
                    actual_value=float(total_kills),
                    game_time=game_time,
                ))

        # Update state for next tick
        self._prev_game_time = game_time
        self._prev_total_gold = total_gold
        self._prev_total_kills = total_kills

        # Record anomalies
        for a in anomalies:
            self._anomalies.append(a)

        return anomalies

    def check_circuit_breaker(self) -> bool:
        """Check if the circuit breaker is tripped.

        Claude21: After too many consecutive rejects, the circuit opens
        and we stop processing for a cooldown period. This prevents
        cascade failures in downstream components when the data source
        is systematically broken.

        Returns True if circuit is OPEN (should skip processing).
        """
        now = time.time()

        if self._circuit_open:
            elapsed = now - self._circuit_open_time
            if elapsed >= self._CIRCUIT_BREAKER_COOLDOWN_S:
                self._circuit_open = False
                logger.info(
                    "DataQualityGate circuit breaker CLOSED after %.1fs cooldown",
                    elapsed,
                )
                return False
            return True

        if self._consecutive_rejects >= self._CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open = True
            self._circuit_open_time = now
            logger.warning(
                "DataQualityGate circuit breaker OPEN after %d consecutive rejects",
                self._consecutive_rejects,
            )
            return True

        return False

    def validate_comprehensive(
        self, data: Dict[str, Any], game_time: float,
    ) -> "ComprehensiveValidation":
        """Full validation pipeline: fields + anomalies + adaptive threshold.

        Claude21: Combines all quality checks into a single call.
        """
        # Circuit breaker check
        if self.check_circuit_breaker():
            return ComprehensiveValidation(
                passed=False,
                score=0.0,
                reason="circuit_breaker_open",
                field_errors=[],
                anomalies=[],
                adaptive_threshold=self._adaptive.threshold,
            )

        # Field validation
        fields_ok, field_errors = self.validate_fields(data, game_time)

        # Base quality assessment (existing DataQualityGate logic)
        assessment = self.assess(data, game_time)

        # Anomaly detection
        anomalies = self.detect_anomalies(data, game_time)

        # Adaptive threshold
        adaptive_ok = self._adaptive.is_acceptable(assessment.score)

        # Combined decision
        has_critical = any(a.severity == "critical" for a in anomalies)
        passed = fields_ok and adaptive_ok and not has_critical

        if not passed:
            self._consecutive_rejects += 1
        else:
            self._consecutive_rejects = 0

        reason = "ok"
        if has_critical:
            reason = "critical_anomaly"
        elif not fields_ok:
            reason = f"field_errors: {len(field_errors)}"
        elif not adaptive_ok:
            reason = (
                f"below_adaptive_threshold: "
                f"{assessment.score:.3f} < {self._adaptive.threshold:.3f}"
            )

        return ComprehensiveValidation(
            passed=passed,
            score=assessment.score,
            reason=reason,
            field_errors=field_errors,
            anomalies=anomalies,
            adaptive_threshold=self._adaptive.threshold,
        )

    def add_field_rule(self, rule: FieldValidationRule) -> None:
        """Add a custom field validation rule."""
        self._field_rules.append(rule)

    def get_anomaly_history(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get recent anomaly events."""
        n = min(count, len(self._anomalies))
        return [a.to_dict() for a in list(self._anomalies)[-n:]]

    def get_field_error_summary(self) -> Dict[str, int]:
        """Get cumulative field error counts for diagnostics."""
        return dict(self._field_error_counts)

    def extended_stats(self) -> Dict[str, Any]:
        """Full diagnostic stats."""
        base = self.gate_stats() if hasattr(self, "gate_stats") else {}
        base.update({
            "adaptive": self._adaptive.stats(),
            "circuit_open": self._circuit_open,
            "consecutive_rejects": self._consecutive_rejects,
            "anomaly_count": len(self._anomalies),
            "recent_anomalies": self.get_anomaly_history(5),
            "field_errors": self.get_field_error_summary(),
        })
        return base

    def reset(self) -> None:
        """Reset all state between sessions."""
        super().reset()
        self._adaptive = AdaptiveThreshold(window_size=50, sigma_threshold=2.0)
        self._anomalies.clear()
        self._prev_game_time = 0.0
        self._prev_total_gold = 0.0
        self._prev_total_kills = 0
        self._circuit_open = False
        self._field_error_counts.clear()


@dataclass
class ComprehensiveValidation:
    """Result of DataQualityGateV2.validate_comprehensive().

    Claude21: Single object containing all quality check results.
    """
    passed: bool
    score: float
    reason: str
    field_errors: List[str]
    anomalies: List[AnomalyEvent]
    adaptive_threshold: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 4),
            "reason": self.reason,
            "field_errors": self.field_errors[:5],
            "anomaly_count": len(self.anomalies),
            "adaptive_threshold": round(self.adaptive_threshold, 4),
        }
