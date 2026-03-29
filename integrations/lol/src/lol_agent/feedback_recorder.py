"""
Feedback Recorder — Tracks user action vs AI suggestion deviation.

Records each advice-action pair, computes match rate and deviation
score for self-evolution feedback loops.

Location: integrations/lol/src/lol_agent/feedback_recorder.py

Reference (拿来主义):
  - operatorRL/modules/evolution_loop_abc.py: record() + fitness computation
  - DI-star/distar/ctools/worker/league/cum_stat.py: cumulative statistics
  - integrations/lol/src/lol_agent/lol_evolution_loop.py: reward tracking
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.feedback_recorder.v1"


class FeedbackRecorder:
    """Records user actions vs AI advice for deviation analysis.

    Each record captures the advised action, actual action, and
    game time. Computes match rate, deviation score.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record(self, advice: str, action: str, game_time: float) -> None:
        """Record an advice-action pair.

        Args:
            advice: AI-suggested action string.
            action: Actual user action string.
            game_time: Game time when action occurred.
        """
        entry = {
            "advice": advice,
            "action": action,
            "game_time": game_time,
            "matched": advice == action,
            "recorded_at": time.time(),
        }
        self._records.append(entry)

        self._fire_evolution("feedback_recorded", {
            "matched": entry["matched"],
            "game_time": game_time,
        })

    def total_count(self) -> int:
        """Total number of recorded feedback entries."""
        return len(self._records)

    def match_rate(self) -> float:
        """Fraction of actions matching advice.

        Returns:
            Float in [0.0, 1.0], or 0.0 if no records.
        """
        if not self._records:
            return 0.0
        matches = sum(1 for r in self._records if r["matched"])
        return matches / len(self._records)

    def deviation_score(self) -> float:
        """Fraction of actions deviating from advice.

        Returns:
            Float in [0.0, 1.0], inverse of match_rate.
        """
        return 1.0 - self.match_rate()

    def get_records(self) -> list[dict[str, Any]]:
        """Return all feedback records."""
        return list(self._records)

    def get_deviations(self) -> list[dict[str, Any]]:
        """Return only records where action != advice."""
        return [r for r in self._records if not r["matched"]]

    def clear(self) -> None:
        """Clear all records."""
        self._records.clear()

    def export_summary(self) -> dict[str, Any]:
        """Export a summary of feedback statistics.

        Returns:
            Dict with total, match_rate, deviation_score.
        """
        return {
            "total": self.total_count(),
            "match_rate": self.match_rate(),
            "deviation_score": self.deviation_score(),
            "deviation_count": len(self.get_deviations()),
        }

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
