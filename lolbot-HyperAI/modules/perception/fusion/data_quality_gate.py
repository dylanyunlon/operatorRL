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
