"""
PostgameDataCollector — Collects postgame statistics and exports training records.

Architecture (拿来主义):
  postgame_data_harvester.py — postgame data collection patterns
  postgame_auto_evaluator.py — automatic postgame evaluation

Location: integrations/lol-history/src/lol_history/postgame_data_collector.py

Design Notes (Knuth-level critique):
  User:
    - Automatic collection after game end (triggered by lifecycle detector).
    - Prediction vs actual comparison for self-evaluation feedback.
    - Training records include full game context for model improvement.
  System:
    - End-of-game stats block parsing from LCU API.
    - Prediction accuracy tracked per-game for evolution feedback.
    - Training records schema-validated before export.
    - Bounded storage: only last N games kept in memory.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.postgame_data_collector.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _EndgameStatsParser:
    """Parses end-of-game statistics from various data sources."""

    REQUIRED_FIELDS = ["game_time", "players", "events"]

    def parse_game_state(self, game_state: Dict[str, Any]) -> Dict[str, Any]:
        """Parse final game state into structured postgame stats."""
        parsed = {
            "game_duration": game_state.get("game_time", 0),
            "map_number": game_state.get("map_number", 11),
            "phase": "postgame",
            "parsed_at": time.monotonic(),
        }

        players = game_state.get("players", [])
        parsed["player_stats"] = []
        for p in players:
            parsed["player_stats"].append({
                "name": p.get("summoner_name", "Unknown"),
                "champion": p.get("champion", "Unknown"),
                "champion_id": p.get("champion_id", 0),
                "team": p.get("team", "Unknown"),
                "position": p.get("position", "Unknown"),
                "kills": p.get("kills", 0),
                "deaths": p.get("deaths", 0),
                "assists": p.get("assists", 0),
                "cs": p.get("cs", 0),
                "gold": p.get("gold", 0),
                "level": p.get("level", 1),
                "items": p.get("items", []),
            })

        events = game_state.get("events", [])
        parsed["event_summary"] = self._summarize_events(events)
        parsed["validation"] = self._validate(parsed)
        return parsed

    def _summarize_events(self, events: List[Dict]) -> Dict[str, Any]:
        type_counts = defaultdict(int)
        for e in events:
            type_counts[e.get("EventName", "Unknown")] += 1
        return {
            "total_events": len(events),
            "type_counts": dict(type_counts),
            "first_event_time": events[0].get("EventTime", 0) if events else 0,
            "last_event_time": events[-1].get("EventTime", 0) if events else 0,
        }

    def _validate(self, parsed: Dict) -> Dict[str, Any]:
        issues = []
        if parsed["game_duration"] <= 0:
            issues.append("game_duration <= 0")
        if not parsed["player_stats"]:
            issues.append("no player stats")
        return {
            "valid": len(issues) == 0,
            "issues": issues,
        }


class _PredictionComparator:
    """Compares pre-game predictions against actual outcomes."""

    def __init__(self) -> None:
        self._comparison_count = 0
        self._accuracy_history: deque = deque(maxlen=200)
        self._cumulative_error = 0.0

    def compare(self, predictions: Dict[str, Any],
                actuals: Dict[str, Any]) -> Dict[str, Any]:
        self._comparison_count += 1
        results = {"comparison_num": self._comparison_count}

        pred_win = predictions.get("win_probability", 0.5)
        actual_win = actuals.get("actual_win", False)
        actual_value = 1.0 if actual_win else 0.0

        win_error = abs(pred_win - actual_value)
        win_correct = (pred_win >= 0.5) == actual_win
        self._cumulative_error += win_error

        results["win_prediction"] = {
            "predicted_probability": pred_win,
            "actual_outcome": actual_win,
            "prediction_correct": win_correct,
            "absolute_error": round(win_error, 4),
            "brier_score": round(win_error ** 2, 4),
        }

        pred_duration = predictions.get("predicted_duration")
        actual_duration = actuals.get("actual_duration")
        if pred_duration and actual_duration:
            dur_error = abs(pred_duration - actual_duration)
            results["duration_prediction"] = {
                "predicted": pred_duration,
                "actual": actual_duration,
                "error_seconds": dur_error,
                "error_pct": round(_safe_div(dur_error, actual_duration) * 100, 1),
            }

        self._accuracy_history.append({
            "correct": win_correct,
            "error": win_error,
            "ts": time.monotonic(),
        })

        rolling = list(self._accuracy_history)[-20:]
        results["rolling_accuracy"] = {
            "window": len(rolling),
            "accuracy": _safe_div(sum(1 for r in rolling if r["correct"]), len(rolling)),
            "avg_error": _safe_div(sum(r["error"] for r in rolling), len(rolling)),
        }

        return results

    def get_stats(self) -> Dict[str, Any]:
        total = len(self._accuracy_history)
        correct = sum(1 for r in self._accuracy_history if r["correct"])
        return {
            "comparisons": self._comparison_count,
            "overall_accuracy": _safe_div(correct, total),
            "cumulative_error": self._cumulative_error,
            "history_size": total,
        }


class _TrainingRecordBuilder:
    """Builds structured training records from postgame data."""

    SCHEMA_VERSION = "2.0"

    REQUIRED_RECORD_FIELDS = [
        "game_id", "game_duration", "player_stats", "event_summary",
    ]

    def __init__(self) -> None:
        self._build_count = 0
        self._validation_failures = 0

    def build(self, game_id: str, postgame_stats: Dict[str, Any],
              predictions: Dict[str, Any] = None,
              suggestions: List[Dict] = None,
              adherence: Dict[str, Any] = None) -> Dict[str, Any]:
        self._build_count += 1

        record = {
            "schema_version": self.SCHEMA_VERSION,
            "game_id": game_id,
            "record_id": hashlib.md5(f"{game_id}_{time.time()}".encode()).hexdigest()[:16],
            "created_at": time.monotonic(),
            "game_duration": postgame_stats.get("game_duration", 0),
            "map_number": postgame_stats.get("map_number", 11),
            "player_stats": postgame_stats.get("player_stats", []),
            "event_summary": postgame_stats.get("event_summary", {}),
        }

        if predictions:
            record["predictions"] = predictions
        if suggestions:
            record["suggestions_given"] = len(suggestions)
            record["suggestion_types"] = list(set(s.get("type", "") for s in suggestions))
        if adherence:
            record["adherence_rate"] = adherence.get("rate", 0.0)
            record["adherence_details"] = adherence

        record["feature_vector"] = self._compute_feature_vector(record)

        validation = self._validate_record(record)
        record["validation"] = validation
        if not validation["valid"]:
            self._validation_failures += 1

        return record

    def _compute_feature_vector(self, record: Dict) -> Dict[str, float]:
        """Compute numerical features for ML training."""
        features = {
            "game_duration_min": record["game_duration"] / 60.0,
            "player_count": len(record.get("player_stats", [])),
            "event_count": record.get("event_summary", {}).get("total_events", 0),
        }

        for ps in record.get("player_stats", []):
            prefix = ps.get("position", "UNK").lower()
            features[f"{prefix}_kda"] = _safe_div(
                ps.get("kills", 0) + ps.get("assists", 0),
                max(1, ps.get("deaths", 1)))
            features[f"{prefix}_cs_per_min"] = _safe_div(
                ps.get("cs", 0), record["game_duration"] / 60.0)
            features[f"{prefix}_gold"] = float(ps.get("gold", 0))

        return features

    def _validate_record(self, record: Dict) -> Dict[str, Any]:
        issues = []
        for field in self.REQUIRED_RECORD_FIELDS:
            if field not in record or not record[field]:
                issues.append(f"missing_field: {field}")
        if record.get("game_duration", 0) < 180:
            issues.append("game_too_short (<3min, likely remake)")
        return {"valid": len(issues) == 0, "issues": issues}

    def get_stats(self) -> Dict[str, Any]:
        return {
            "build_count": self._build_count,
            "validation_failures": self._validation_failures,
            "schema_version": self.SCHEMA_VERSION,
        }


class _TrainingDataStore:
    """In-memory store for training records with export capability."""

    def __init__(self, max_records: int = 100) -> None:
        self._records: deque = deque(maxlen=max_records)
        self._game_index: Dict[str, int] = {}
        self._export_count = 0

    def store(self, record: Dict[str, Any]) -> int:
        idx = len(self._records)
        self._records.append(record)
        game_id = record.get("game_id", "")
        if game_id:
            self._game_index[game_id] = idx
        return idx

    def get_by_game_id(self, game_id: str) -> Optional[Dict]:
        idx = self._game_index.get(game_id)
        if idx is not None:
            records_list = list(self._records)
            if idx < len(records_list):
                return records_list[idx]
        return None

    def export_all(self) -> List[Dict]:
        self._export_count += 1
        return [r for r in self._records if r.get("validation", {}).get("valid", False)]

    def export_json(self) -> str:
        self._export_count += 1
        valid = self.export_all()
        return json.dumps(valid, default=str, indent=2)

    def get_stats(self) -> Dict[str, Any]:
        valid = sum(1 for r in self._records if r.get("validation", {}).get("valid"))
        return {
            "total_records": len(self._records),
            "valid_records": valid,
            "indexed_games": len(self._game_index),
            "export_count": self._export_count,
        }


class PostgameDataCollector:
    """Collects postgame data, compares predictions, exports training records.

    Public API: collect_endgame, compare_predictions, export_training_record,
                get_game_record, get_all_records, get_prediction_accuracy, get_stats
    """

    def __init__(self, max_games: int = 100) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._collection_count = 0
        self._parser = _EndgameStatsParser()
        self._comparator = _PredictionComparator()
        self._builder = _TrainingRecordBuilder()
        self._store = _TrainingDataStore(max_records=max_games)
        self._last_collection: Optional[Dict] = None

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def collect_endgame(self, game_state: Dict[str, Any],
                        event_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Collect and parse end-of-game statistics."""
        self._op_count += 1
        self._collection_count += 1

        if event_data and "Events" in event_data:
            game_state = {**game_state, "events": event_data["Events"]}

        parsed = self._parser.parse_game_state(game_state)
        self._last_collection = parsed

        self._fire("endgame_collected", {
            "collection_num": self._collection_count,
            "valid": parsed["validation"]["valid"],
            "duration": parsed["game_duration"],
        })

        return {
            "status": "ok",
            "collection_num": self._collection_count,
            "postgame_stats": parsed,
        }

    def compare_predictions(self, predictions: Dict[str, Any],
                            actuals: Dict[str, Any]) -> Dict[str, Any]:
        """Compare pregame predictions against actual outcomes."""
        self._op_count += 1
        comparison = self._comparator.compare(predictions, actuals)
        self._fire("prediction_compared", {
            "correct": comparison.get("win_prediction", {}).get("prediction_correct"),
        })
        return {"status": "ok", "comparison": comparison}

    def export_training_record(self, game_id: str,
                               predictions: Dict[str, Any] = None,
                               suggestions: List[Dict] = None,
                               adherence: Dict[str, Any] = None) -> Dict[str, Any]:
        """Build and store a training record for the game."""
        self._op_count += 1
        postgame = self._last_collection or {}
        record = self._builder.build(game_id, postgame, predictions,
                                     suggestions, adherence)
        idx = self._store.store(record)
        return {
            "status": "ok",
            "game_id": game_id,
            "record_index": idx,
            "valid": record["validation"]["valid"],
            "feature_count": len(record.get("feature_vector", {})),
        }

    def get_game_record(self, game_id: str) -> Dict[str, Any]:
        self._op_count += 1
        record = self._store.get_by_game_id(game_id)
        return {
            "status": "ok",
            "game_id": game_id,
            "found": record is not None,
            "record": record,
        }

    def get_all_records(self) -> Dict[str, Any]:
        self._op_count += 1
        records = self._store.export_all()
        return {
            "status": "ok",
            "record_count": len(records),
            "records": records,
        }

    def get_prediction_accuracy(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "accuracy_stats": self._comparator.get_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "collection_count": self._collection_count,
            "parser_validation": self._last_collection.get("validation") if self._last_collection else None,
            "comparator": self._comparator.get_stats(),
            "builder": self._builder.get_stats(),
            "store": self._store.get_stats(),
        }
