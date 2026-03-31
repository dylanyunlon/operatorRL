"""
PostgameDataHarvester — Harvests complete post-game data for the feedback loop.

Architecture (拿来主义):
  postgame_evolution_analyzer.py + history_feedback_loop_orchestrator.py（M625）

Location: integrations/lol-history/src/lol_history/postgame_data_harvester.py

Design Notes (Knuth-level critique):
  User:
    - harvest() accepts partial data — missing fields are logged, not rejected.
    - get_harvest_report always returns valid dict even with zero harvests.
    - Each harvest record includes completeness score for downstream quality gates.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - _harvests bounded by _max_harvests to prevent unbounded growth.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.postgame_data_harvester.v1"

_REQUIRED_FIELDS = [
    "match_id", "game_duration", "win", "champion_id",
    "kills", "deaths", "assists", "cs", "gold_earned",
]

_OPTIONAL_FIELDS = [
    "vision_score", "damage_dealt", "damage_taken",
    "wards_placed", "wards_destroyed", "turrets_destroyed",
    "dragons_taken", "barons_taken", "team_comp",
    "enemy_team_comp", "item_build", "runes",
]


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class PostgameDataHarvester:
    """Harvests complete post-game data for the feedback loop.

    Public API
    ----------
    harvest             — ingest a single postgame record
    harvest_batch       — ingest multiple postgame records
    get_harvest_report  — summary of all harvested data
    get_latest          — retrieve N most recent harvests
    reset               — clear all state

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, max_harvests: int = 5000) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._harvests: List[Dict[str, Any]] = []
        self._max_harvests: int = max_harvests
        self._error_count: int = 0
        self._field_miss_counts: Dict[str, int] = defaultdict(int)

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def harvest(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Ingest a single postgame record.

        Parameters
        ----------
        data : dict
            Post-game match data.

        Returns
        -------
        dict  with status, completeness, missing_fields
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        # Check field completeness
        present = 0
        missing: List[str] = []
        all_fields = _REQUIRED_FIELDS + _OPTIONAL_FIELDS
        for f in all_fields:
            if f in data and data[f] is not None:
                present += 1
            else:
                missing.append(f)
                self._field_miss_counts[f] += 1

        completeness = _safe_div(present, len(all_fields))

        record = {
            **data,
            "harvested_at": time.time(),
            "completeness": round(completeness, 4),
            "missing_fields": missing,
        }

        if len(self._harvests) >= self._max_harvests:
            self._harvests.pop(0)
        self._harvests.append(record)

        elapsed = time.time() - _start
        self._fire("harvest_completed", {"elapsed": elapsed, "completeness": completeness})
        return {"status": "ok", "op": "harvest",
                "completeness": round(completeness, 4),
                "missing_fields": missing}

    # ------------------------------------------------------------------ #

    def harvest_batch(self, records: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Ingest multiple postgame records.

        Parameters
        ----------
        records : list of dict

        Returns
        -------
        dict  with status, processed, avg_completeness
        """
        self._op_count += 1
        _start = time.time()
        if records is None:
            records = []

        total_completeness = 0.0
        for rec in records:
            result = self.harvest(rec)
            total_completeness += result.get("completeness", 0.0)

        avg = _safe_div(total_completeness, len(records))

        elapsed = time.time() - _start
        self._fire("harvest_batch_completed", {"elapsed": elapsed, "count": len(records)})
        return {"status": "ok", "op": "harvest_batch",
                "processed": len(records), "avg_completeness": round(avg, 4)}

    # ------------------------------------------------------------------ #

    def get_harvest_report(self) -> Dict[str, Any]:
        """Summary of all harvested data.

        Returns
        -------
        dict  with total, avg_completeness, most_missing_fields, win_rate
        """
        self._op_count += 1
        _start = time.time()

        total = len(self._harvests)
        if total == 0:
            return {"status": "ok", "op": "get_harvest_report",
                    "total": 0, "avg_completeness": 0.0,
                    "most_missing_fields": [], "win_rate": 0.0}

        avg_comp = sum(h.get("completeness", 0) for h in self._harvests) / total
        wins = sum(1 for h in self._harvests if h.get("win"))
        win_rate = _safe_div(wins, total)

        sorted_missing = sorted(self._field_miss_counts.items(), key=lambda x: -x[1])
        top_missing = [{"field": f, "count": c} for f, c in sorted_missing[:5]]

        elapsed = time.time() - _start
        self._fire("get_harvest_report_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "get_harvest_report",
                "total": total, "avg_completeness": round(avg_comp, 4),
                "most_missing_fields": top_missing,
                "win_rate": round(win_rate, 4)}

    # ------------------------------------------------------------------ #

    def get_latest(self, n: int = 10) -> Dict[str, Any]:
        """Retrieve N most recent harvests.

        Returns
        -------
        dict  with status, records
        """
        self._op_count += 1
        records = self._harvests[-n:] if self._harvests else []
        return {"status": "ok", "op": "get_latest", "records": records}

    # ------------------------------------------------------------------ #

    def reset(self) -> Dict[str, Any]:
        """Clear all state."""
        self._op_count += 1
        self._harvests.clear()
        self._error_count = 0
        self._field_miss_counts.clear()
        self._fire("reset_completed", {})
        return {"status": "ok", "op": "reset"}
