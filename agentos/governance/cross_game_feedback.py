"""
Cross-Game Feedback — Multi-game feedback aggregation + learning transfer.

Aggregates feedback data across all game integrations (LoL, Dota2, Mahjong).
Computes per-game and global match rates, and generates cross-game
transfer insights for shared strategic patterns.

Location: agentos/governance/cross_game_feedback.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/feedback_recorder.py: match_rate + deviation pattern
  - modules/evolution_loop_abc.py: record/fitness pattern
  - agentos/governance/telemetry_collector.py: cross-module metric aggregation
  - DI-star/distar/ctools/utils/log_helper.py: multi-source log aggregation
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.cross_game_feedback.v1"


class CrossGameFeedback:
    """Multi-game feedback aggregation and learning transfer.

    Collects feedback records per game, computes match rates,
    and generates transfer insights between games.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._feedback: dict[str, list[dict[str, Any]]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record(self, game: str, feedback: dict[str, Any]) -> None:
        """Record a feedback entry for a game.

        Args:
            game: Game identifier.
            feedback: Feedback dict with at least 'match' bool key.
        """
        if game not in self._feedback:
            self._feedback[game] = []
        self._feedback[game].append(dict(feedback))
        self._fire_evolution({"action": "record", "game": game})

    def feedback_count(self, game: str) -> int:
        """Return number of feedback records for a game."""
        return len(self._feedback.get(game, []))

    def match_rate(self, game: str) -> float:
        """Compute match rate for a game (matched / total).

        Returns:
            Float 0.0-1.0, or 0.0 if no records.
        """
        records = self._feedback.get(game, [])
        if not records:
            return 0.0
        matches = sum(1 for r in records if r.get("match", False))
        return matches / len(records)

    def global_match_rate(self) -> float:
        """Compute global match rate across all games.

        Returns:
            Float 0.0-1.0, or 0.0 if no records.
        """
        all_records = []
        for records in self._feedback.values():
            all_records.extend(records)
        if not all_records:
            return 0.0
        matches = sum(1 for r in all_records if r.get("match", False))
        return matches / len(all_records)

    def list_games(self) -> list[str]:
        """List all games with recorded feedback."""
        return sorted(self._feedback.keys())

    def export_summary(self) -> dict[str, Any]:
        """Export summary of feedback across all games."""
        summary = {}
        for game in self._feedback:
            summary[game] = {
                "count": self.feedback_count(game),
                "match_rate": self.match_rate(game),
            }
        return summary

    def transfer_insights(
        self, source_game: str, target_game: str
    ) -> dict[str, Any]:
        """Generate transfer learning insights from source to target game.

        Analyzes feedback patterns from source game that might be
        applicable to the target game (e.g., positioning patterns,
        risk assessment, timing decisions).

        Args:
            source_game: Game to learn from.
            target_game: Game to apply insights to.

        Returns:
            Dict with transferable insights.
        """
        source_records = self._feedback.get(source_game, [])
        if not source_records:
            return {
                "source_game": source_game,
                "from_game": source_game,
                "target_game": target_game,
                "insights": [],
                "message": "No source data available",
            }

        # Analyze categories of successful advice
        matched = [r for r in source_records if r.get("match", False)]
        unmatched = [r for r in source_records if not r.get("match", False)]

        categories: dict[str, int] = {}
        for r in matched:
            cat = r.get("category", "general")
            categories[cat] = categories.get(cat, 0) + 1

        # Sort by frequency
        sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)

        return {
            "source_game": source_game,
            "from_game": source_game,
            "target_game": target_game,
            "source_match_rate": self.match_rate(source_game),
            "total_source_records": len(source_records),
            "matched_count": len(matched),
            "unmatched_count": len(unmatched),
            "top_categories": sorted_cats[:5],
            "insights": [
                f"Pattern '{cat}' successful {count} times in {source_game}"
                for cat, count in sorted_cats[:3]
            ],
        }

    # --- Evolution pattern ---
    def _fire_evolution(self, detail: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "key": _EVOLUTION_KEY,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                logger.warning("Evolution callback error (cross_game_feedback)")
