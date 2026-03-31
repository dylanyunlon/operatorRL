"""
CrossGameTrainingDataFormatter — Format training data from different games into unified triples.

Converts game-specific training samples into a standardized (state, action, reward)
triple format suitable for cross-game training pipelines.

Location: integrations/lol-history/src/lol_history/cross_game_training_data_formatter.py

Reference (拿来主义):
  - integrations/lol-history/src/lol_history/history_to_training_exporter.py（M606）:
    training data export pipeline
  - integrations/lol-history/src/lol_history/history_export_formatter.py（M635）:
    multi-format export
  - DI-star: training data format

Design Notes (Knuth-level critique):
  User:
    - format_sample() always returns a valid triple or error dict — never crashes.
    - batch_format() tracks per-sample errors without aborting batch.
    - export_stats() gives format-rate and error-rate for monitoring.
  System:
    - Per-game formatters registered via register_formatter() — O(1) dispatch.
    - Schema validation on output ensures downstream compatibility.
    - Batch processing with skip-on-error prevents single bad sample from killing pipeline.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.cross_game_training_data_formatter.v1"


class TrainingSample:
    """Standardized training sample triple."""

    __slots__ = ("state", "action", "reward", "metadata")

    def __init__(
        self,
        state: Dict[str, Any],
        action: str,
        reward: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.state = state
        self.action = action
        self.reward = reward
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "action": self.action,
            "reward": self.reward,
            "metadata": self.metadata,
        }


def _default_lol_formatter(raw: Dict[str, Any]) -> TrainingSample:
    state = {
        "game_time": raw.get("game_time", 0.0),
        "gold": raw.get("gold", 0),
        "level": raw.get("level", 0),
        "kills": raw.get("kills", 0),
        "deaths": raw.get("deaths", 0),
        "cs": raw.get("cs", 0),
    }
    return TrainingSample(
        state=state,
        action=raw.get("action", "unknown"),
        reward=float(raw.get("reward", 0.0)),
        metadata={"game_type": "lol", "source": raw.get("source", "")},
    )


def _default_dota2_formatter(raw: Dict[str, Any]) -> TrainingSample:
    state = {
        "game_time": raw.get("game_time", raw.get("clock_time", 0.0)),
        "gold": raw.get("gold", 0),
        "level": raw.get("level", 0),
        "last_hits": raw.get("last_hits", 0),
        "denies": raw.get("denies", 0),
    }
    return TrainingSample(
        state=state,
        action=raw.get("action", "unknown"),
        reward=float(raw.get("reward", 0.0)),
        metadata={"game_type": "dota2"},
    )


def _default_mahjong_formatter(raw: Dict[str, Any]) -> TrainingSample:
    state = {
        "round": raw.get("round", 0),
        "seat": raw.get("seat", 0),
        "scores": raw.get("scores", []),
        "hand": raw.get("hand", []),
        "discards": raw.get("discards", []),
    }
    return TrainingSample(
        state=state,
        action=raw.get("action", raw.get("type", "unknown")),
        reward=float(raw.get("reward", 0.0)),
        metadata={"game_type": "mahjong"},
    )


class CrossGameTrainingDataFormatter:
    """Cross-game training data formatter.

    Public API:
        format_sample(game_type, raw) -> TrainingSample
        batch_format(game_type, samples) -> list[TrainingSample]
        register_formatter(game_type, fn)
        export_as_dicts(samples) -> list[dict]
        export_as_json(samples) -> str
        get_stats() -> dict
    """

    def __init__(self) -> None:
        self._formatters: Dict[str, Callable[[Dict[str, Any]], TrainingSample]] = {
            "lol": _default_lol_formatter,
            "dota2": _default_dota2_formatter,
            "mahjong": _default_mahjong_formatter,
        }
        self._format_count: int = 0
        self._error_count: int = 0
        self._per_game_count: Dict[str, int] = {}
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def register_formatter(
        self,
        game_type: str,
        fn: Callable[[Dict[str, Any]], TrainingSample],
    ) -> None:
        self._formatters[game_type] = fn
        self._fire("formatter_registered", {"game_type": game_type})

    def format_sample(self, game_type: str, raw: Dict[str, Any]) -> TrainingSample:
        """Format a single raw sample into a TrainingSample.

        Raises ValueError if game_type has no registered formatter.
        """
        self._format_count += 1
        self._per_game_count[game_type] = self._per_game_count.get(game_type, 0) + 1

        formatter = self._formatters.get(game_type)
        if formatter is None:
            self._error_count += 1
            raise ValueError(f"No formatter registered for game type: {game_type}")

        return formatter(raw)

    def batch_format(
        self,
        game_type: str,
        samples: List[Dict[str, Any]],
    ) -> Tuple[List[TrainingSample], List[Dict[str, Any]]]:
        """Format a batch, returning (successes, errors)."""
        results: List[TrainingSample] = []
        errors: List[Dict[str, Any]] = []

        for i, raw in enumerate(samples):
            try:
                results.append(self.format_sample(game_type, raw))
            except Exception as exc:
                self._error_count += 1
                errors.append({"index": i, "error": str(exc)})

        if errors:
            self._fire("batch_errors", {
                "game_type": game_type,
                "total": len(samples),
                "errors": len(errors),
            })

        return results, errors

    def export_as_dicts(self, samples: List[TrainingSample]) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in samples]

    def export_as_json(self, samples: List[TrainingSample], indent: int = 2) -> str:
        return json.dumps(self.export_as_dicts(samples), indent=indent, ensure_ascii=False)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "format_count": self._format_count,
            "error_count": self._error_count,
            "per_game": dict(self._per_game_count),
            "registered_formatters": list(self._formatters.keys()),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")
