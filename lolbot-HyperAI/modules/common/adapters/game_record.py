"""
modules/common/adapters/game_record.py — Structured game session recorder.
============================================================================
Claude18 · Apollo cyber/record pattern for game session recording

Problem: Evolution layer evaluates fitness but has no structured record
of what happened during the game. The only recording is Transport's raw
JSONL which dumps everything. We need a structured session record with
key events, prediction accuracy, and strategy effectiveness.

Solution (Apollo cyber/record pattern):
    查看 Apollo cyber/record/record_writer.h 上现有录制模块的实现方式,
    理解其模式, 特别是 Channel + timestamp 索引 是如何组织录制数据的。
    从 Apollo RecordWriter 的 channel-based recording 这个好例子开始。
    然后, 遵循该模式实现一个 GameRecord, 让 evolution 可以 回放任意
    一局游戏的关键时刻, 并能 评估策略推荐的准确性。

File location: lolbot-HyperAI/modules/common/adapters/game_record.py
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PredictionRecord:
    """Snapshot of a prediction at a specific game time."""
    game_time: float
    win_probability: float
    confidence: float
    teamfight_action: str = ""
    top_features: List[str] = field(default_factory=list)


@dataclass
class StrategyRecord:
    """Snapshot of a strategy recommendation."""
    game_time: float
    action: str
    reasoning: str
    confidence: float
    was_followed: bool = False  # Did the player act on it?


@dataclass
class EventRecord:
    """A game event with context."""
    game_time: float
    event_type: str
    killer: str = ""
    victim: str = ""
    gold_diff_at_event: float = 0.0
    win_prob_at_event: float = 0.5


@dataclass
class GameRecord:
    """Complete structured record of a game session.

    Captures the full timeline of predictions, strategies, and events
    for post-game analysis and evolution fitness evaluation.
    """
    session_id: str = ""
    start_real_time: float = field(default_factory=time.time)
    end_real_time: float = 0.0
    game_duration_s: float = 0.0
    data_source: str = ""

    # Player info
    active_player: str = ""
    active_champion: str = ""
    active_team: str = ""

    # Timeline records
    predictions: List[PredictionRecord] = field(default_factory=list)
    strategies: List[StrategyRecord] = field(default_factory=list)
    events: List[EventRecord] = field(default_factory=list)

    # Summary statistics
    final_win_prob: float = 0.5
    prediction_accuracy: float = 0.0  # How close final prob was to outcome
    total_advice_given: int = 0
    total_events: int = 0
    gold_diff_final: float = 0.0

    # Evolution metadata
    generation_id: str = ""
    fitness_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "game_duration_s": round(self.game_duration_s, 1),
            "data_source": self.data_source,
            "active_player": self.active_player,
            "active_champion": self.active_champion,
            "active_team": self.active_team,
            "prediction_count": len(self.predictions),
            "strategy_count": len(self.strategies),
            "event_count": len(self.events),
            "final_win_prob": round(self.final_win_prob, 4),
            "prediction_accuracy": round(self.prediction_accuracy, 4),
            "gold_diff_final": round(self.gold_diff_final, 0),
            "generation_id": self.generation_id,
            "fitness_score": round(self.fitness_score, 4),
        }


class GameRecorder:
    """Records game session data for post-game analysis.

    Designed to be called from MainLoop's session management.
    Sub-samples predictions/strategies to avoid excessive memory.

    Usage::
        recorder = GameRecorder()
        recorder.start_session("session_123", data_source="testdata")
        # During game:
        recorder.record_prediction(game_time, win_prob, confidence)
        recorder.record_strategy(game_time, action, reasoning, conf)
        recorder.record_event(game_time, "ChampionKill", killer, victim)
        # After game:
        record = recorder.end_session(game_duration, gold_diff)
        recorder.save(record, output_dir)
    """

    # Sub-sample predictions: keep one every N seconds
    _PRED_SAMPLE_INTERVAL_S = 10.0
    _MAX_PREDICTIONS = 300
    _MAX_STRATEGIES = 200
    _MAX_EVENTS = 500

    def __init__(self) -> None:
        self._current: Optional[GameRecord] = None
        self._last_pred_time: float = 0.0
        self._session_count: int = 0

    @property
    def is_recording(self) -> bool:
        return self._current is not None

    def start_session(
        self,
        session_id: str,
        data_source: str = "",
        generation_id: str = "",
    ) -> None:
        """Start recording a new game session."""
        self._current = GameRecord(
            session_id=session_id,
            data_source=data_source,
            generation_id=generation_id,
        )
        self._last_pred_time = 0.0
        self._session_count += 1
        logger.info("GameRecorder: started session %s", session_id)

    def set_player_info(
        self,
        name: str,
        champion: str,
        team: str,
    ) -> None:
        if self._current:
            self._current.active_player = name
            self._current.active_champion = champion
            self._current.active_team = team

    def record_prediction(
        self,
        game_time: float,
        win_probability: float,
        confidence: float,
        teamfight_action: str = "",
    ) -> None:
        """Record a prediction snapshot (sub-sampled)."""
        if self._current is None:
            return
        if game_time - self._last_pred_time < self._PRED_SAMPLE_INTERVAL_S:
            return
        if len(self._current.predictions) >= self._MAX_PREDICTIONS:
            return

        self._last_pred_time = game_time
        self._current.predictions.append(PredictionRecord(
            game_time=game_time,
            win_probability=win_probability,
            confidence=confidence,
            teamfight_action=teamfight_action,
        ))

    def record_strategy(
        self,
        game_time: float,
        action: str,
        reasoning: str,
        confidence: float,
    ) -> None:
        if self._current is None:
            return
        if len(self._current.strategies) >= self._MAX_STRATEGIES:
            return
        self._current.strategies.append(StrategyRecord(
            game_time=game_time,
            action=action,
            reasoning=reasoning,
            confidence=confidence,
        ))
        self._current.total_advice_given += 1

    def record_event(
        self,
        game_time: float,
        event_type: str,
        killer: str = "",
        victim: str = "",
        gold_diff: float = 0.0,
        win_prob: float = 0.5,
    ) -> None:
        if self._current is None:
            return
        if len(self._current.events) >= self._MAX_EVENTS:
            return
        self._current.events.append(EventRecord(
            game_time=game_time,
            event_type=event_type,
            killer=killer,
            victim=victim,
            gold_diff_at_event=gold_diff,
            win_prob_at_event=win_prob,
        ))
        self._current.total_events += 1

    def end_session(
        self,
        game_duration_s: float = 0.0,
        gold_diff_final: float = 0.0,
        won: Optional[bool] = None,
    ) -> Optional[GameRecord]:
        """End the session and compute summary statistics."""
        if self._current is None:
            return None

        record = self._current
        record.end_real_time = time.time()
        record.game_duration_s = game_duration_s
        record.gold_diff_final = gold_diff_final

        # Final win probability from last prediction
        if record.predictions:
            record.final_win_prob = record.predictions[-1].win_probability

        # Prediction accuracy (if we know the outcome)
        if won is not None:
            actual = 1.0 if won else 0.0
            record.prediction_accuracy = 1.0 - abs(
                record.final_win_prob - actual
            )

        self._current = None
        logger.info(
            "GameRecorder: ended session %s (%.0fs, %d predictions, "
            "%d strategies, %d events)",
            record.session_id, game_duration_s,
            len(record.predictions), len(record.strategies),
            len(record.events),
        )
        return record

    def save(
        self,
        record: GameRecord,
        output_dir: str = "data/game_records",
    ) -> Optional[Path]:
        """Save game record to JSON file."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        filepath = out / f"{record.session_id}.json"

        try:
            data = {
                "summary": record.to_dict(),
                "predictions": [
                    {
                        "t": round(p.game_time, 1),
                        "wp": round(p.win_probability, 4),
                        "c": round(p.confidence, 3),
                    }
                    for p in record.predictions
                ],
                "strategies": [
                    {
                        "t": round(s.game_time, 1),
                        "action": s.action,
                        "conf": round(s.confidence, 3),
                    }
                    for s in record.strategies
                ],
                "events": [
                    {
                        "t": round(e.game_time, 1),
                        "type": e.event_type,
                        "killer": e.killer,
                        "victim": e.victim,
                    }
                    for e in record.events
                ],
            }
            filepath.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("GameRecorder: saved %s", filepath)
            return filepath
        except IOError as exc:
            logger.error("GameRecorder: save failed: %s", exc)
            return None

    def stats(self) -> Dict[str, Any]:
        return {
            "session_count": self._session_count,
            "recording": self.is_recording,
            "current_session": (
                self._current.session_id if self._current else None
            ),
        }
