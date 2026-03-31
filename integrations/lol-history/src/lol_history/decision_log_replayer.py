"""
DecisionLogReplayer — Replays decision logs for post-game review.

Architecture (拿来主义):
  protocol_replay_synchronizer.py（M652）— time-axis synchronized replay
  replay_decision_auditor.py（M612）— frame-by-frame decision comparison

Location: integrations/lol-history/src/lol_history/decision_log_replayer.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.decision_log_replayer.v1"

class DecisionLogReplayer:
    """Replays decision log timeline: state→analysis→action→feedback.

    Public API: load_log, seek, next_entry, filter_by_type, get_summary, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._log: List[Dict[str, Any]] = []
        self._cursor = 0
        self._replay_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def load_log(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._op_count += 1
        self._log = sorted(entries, key=lambda e: e.get("timestamp", 0))
        self._cursor = 0
        return {"status": "ok", "entries": len(self._log)}

    def seek(self, game_time: float) -> Dict[str, Any]:
        self._op_count += 1
        for i, e in enumerate(self._log):
            if e.get("game_time", e.get("timestamp", 0)) >= game_time:
                self._cursor = i
                return {"status": "ok", "cursor": i, "game_time": game_time}
        self._cursor = len(self._log)
        return {"status": "ok", "cursor": self._cursor, "game_time": game_time, "at_end": True}

    def next_entry(self) -> Dict[str, Any]:
        self._op_count += 1
        self._replay_count += 1
        if self._cursor >= len(self._log):
            return {"status": "end_of_log"}
        entry = self._log[self._cursor]
        self._cursor += 1
        return {"status": "ok", "entry": entry, "cursor": self._cursor, "remaining": len(self._log) - self._cursor}

    def filter_by_type(self, decision_type: str) -> List[Dict[str, Any]]:
        self._op_count += 1
        return [e for e in self._log if e.get("type") == decision_type or e.get("intent") == decision_type]

    def get_summary(self) -> Dict[str, Any]:
        self._op_count += 1
        types = {}
        for e in self._log: types[e.get("type", "unknown")] = types.get(e.get("type", "unknown"), 0) + 1
        return {"status": "ok", "total_entries": len(self._log), "type_distribution": types,
                "duration_s": (self._log[-1].get("timestamp", 0) - self._log[0].get("timestamp", 0)) if len(self._log) > 1 else 0}

    def get_stats(self) -> Dict[str, Any]:
        return {"entries": len(self._log), "cursor": self._cursor, "replays": self._replay_count, "total_ops": self._op_count}

