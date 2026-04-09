"""
modules/control/dispatch/effectiveness_tracker.py
Action effectiveness tracking. Verbatim from Claude25 (Claude17).
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional


class ActionEffectivenessTracker:
    """Tracks whether dispatched actions correlate with positive outcomes."""

    def __init__(self, outcome_window_s: float = 60.0) -> None:
        self._outcome_window_s = outcome_window_s
        self._pending_actions: List[Dict[str, Any]] = []
        self._scored_actions: List[Dict[str, Any]] = []
        self._effective_count: int = 0
        self._ineffective_count: int = 0

    def record_action(self, action_text: str, category: str,
                      expected_outcome: str = "", game_time: float = 0.0) -> None:
        self._pending_actions.append({
            "ts": time.time(), "game_time": game_time,
            "action": action_text[:100], "category": category,
            "expected_outcome": expected_outcome, "scored": False,
        })
        if len(self._pending_actions) > 200:
            self._pending_actions = self._pending_actions[-100:]

    def score_against_events(self, events: List[Any]) -> int:
        now = time.time()
        scored = 0
        for action in self._pending_actions:
            if action["scored"]:
                continue
            if now - action["ts"] > self._outcome_window_s:
                action["scored"] = True
                action["result"] = "expired"
                self._ineffective_count += 1
                scored += 1
                continue
            expected = action["expected_outcome"].lower()
            if not expected:
                continue
            for event in events:
                event_type = ""
                if hasattr(event, 'event_type'):
                    et = event.event_type
                    event_type = et.value if hasattr(et, 'value') else str(et)
                if expected in event_type.lower():
                    action["scored"] = True
                    action["result"] = "effective"
                    self._effective_count += 1
                    scored += 1
                    break
        self._scored_actions.extend(a for a in self._pending_actions if a["scored"])
        self._pending_actions = [a for a in self._pending_actions if not a["scored"]]
        if len(self._scored_actions) > 500:
            self._scored_actions = self._scored_actions[-250:]
        return scored

    @property
    def effectiveness_rate(self) -> float:
        total = self._effective_count + self._ineffective_count
        return round(self._effective_count / total, 4) if total else 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "pending_actions": len(self._pending_actions),
            "scored_actions": len(self._scored_actions),
            "effective_count": self._effective_count,
            "ineffective_count": self._ineffective_count,
            "effectiveness_rate": self.effectiveness_rate,
        }
