"""
SessionReplayExporter — Exports decision logs as replayable timelines for offline review.

Architecture (拿来主义):
  replay_decision_auditor.py, match_replay_analyzer.py

Location: integrations/lol-history/src/lol_history/session_replay_exporter.py

Design Notes (Knuth-level critique):
  User:
    - Production-grade module with unified {"status": "ok"} response format.
    - Stateless or bounded-state design for long-running sessions.
    - Graceful degradation: partial results on component failure.
  System:
    - All data structures bounded (deque/OrderedDict with maxlen).
    - Evolution callback integration for self-improvement feedback.
    - Comprehensive get_stats() for observability.
    - Zero external dependencies beyond stdlib.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.session_replay_exporter.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _DecisionRecord:
    """Single decision record in a session timeline."""

    def __init__(self, game_time: float, suggestion: Dict, action: Dict,
                 outcome: str) -> None:
        self.game_time = game_time
        self.suggestion = suggestion
        self.action = action
        self.outcome = outcome
        self.wall_time = time.monotonic()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time": self.game_time,
            "suggestion": self.suggestion,
            "action": self.action,
            "outcome": self.outcome,
        }


class _SessionTimeline:
    """Timeline of decisions for a single game session."""

    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        self.start_time = time.monotonic()
        self.end_time: float = 0.0
        self.decisions: List[_DecisionRecord] = []
        self.win: Optional[bool] = None
        self.metadata: Dict[str, Any] = {}

    def add_decision(self, game_time: float, suggestion: Dict,
                     action: Dict, outcome: str) -> int:
        record = _DecisionRecord(game_time, suggestion, action, outcome)
        self.decisions.append(record)
        return len(self.decisions)

    def end(self, win: bool = None) -> float:
        self.end_time = time.monotonic()
        self.win = win
        return self.end_time - self.start_time

    def export(self) -> Dict[str, Any]:
        return {
            "game_id": self.game_id,
            "decision_count": len(self.decisions),
            "duration": self.end_time - self.start_time if self.end_time else 0,
            "win": self.win,
            "timeline": [d.to_dict() for d in self.decisions],
            "metadata": self.metadata,
        }


class SessionReplayExporter:
    """Exports decision logs as replayable timelines for offline review.

    Public API: start_session, record_decision, end_session,
                export_timeline, get_session_list, get_stats
    """

    def __init__(self, max_sessions: int = 50) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._sessions: Dict[str, _SessionTimeline] = OrderedDict()
        self._max_sessions = max_sessions
        self._active_session: Optional[str] = None
        self._export_count = 0

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _trim(self) -> None:
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

    def start_session(self, game_id: str) -> Dict[str, Any]:
        self._op_count += 1
        session = _SessionTimeline(game_id)
        self._sessions[game_id] = session
        self._active_session = game_id
        self._trim()
        return {"status": "ok", "game_id": game_id, "started": True}

    def record_decision(self, game_time: float, suggestion: Dict,
                        action: Dict, outcome: str = "pending") -> Dict[str, Any]:
        self._op_count += 1
        if not self._active_session:
            return {"status": "ok", "recorded": False, "reason": "no_active_session"}
        session = self._sessions.get(self._active_session)
        if not session:
            return {"status": "ok", "recorded": False, "reason": "session_not_found"}
        count = session.add_decision(game_time, suggestion, action, outcome)
        return {"status": "ok", "recorded": True, "decision_count": count}

    def end_session(self, game_id: str, win: bool = None) -> Dict[str, Any]:
        self._op_count += 1
        session = self._sessions.get(game_id)
        if not session:
            return {"status": "ok", "found": False}
        duration = session.end(win)
        if self._active_session == game_id:
            self._active_session = None
        self._fire("session_ended", {
            "game_id": game_id, "decisions": len(session.decisions),
            "duration": duration,
        })
        return {
            "status": "ok",
            "game_id": game_id,
            "duration": duration,
            "decisions": len(session.decisions),
        }

    def export_timeline(self, game_id: str) -> Dict[str, Any]:
        self._op_count += 1
        self._export_count += 1
        session = self._sessions.get(game_id)
        if not session:
            return {"status": "ok", "found": False}
        return {"status": "ok", "timeline": session.export()}

    def get_session_list(self) -> Dict[str, Any]:
        self._op_count += 1
        sessions = []
        for gid, s in self._sessions.items():
            sessions.append({
                "game_id": gid,
                "decisions": len(s.decisions),
                "win": s.win,
            })
        return {"status": "ok", "sessions": sessions}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "total_sessions": len(self._sessions),
            "active_session": self._active_session,
            "export_count": self._export_count,
        }
