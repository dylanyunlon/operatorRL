"""
Opponent Timing Tracker — Precise operation timestamp capture.

Tracks opponent actions with precise timestamps, calculates reaction times,
estimates ability cooldowns, computes action frequencies, and detects
timing patterns.

Location: extensions/fiddler-bridge/src/fiddler_bridge/opponent_timing_tracker.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.opponent_timing_tracker.v1"


class OpponentTimingTracker:
    """Precise opponent operation timing tracker.

    Records timestamped actions, calculates reaction times,
    estimates cooldowns, and detects behavioral timing patterns.
    """

    def __init__(self) -> None:
        # champion -> list of {"action": str, "timestamp": float}
        self._actions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_action(
        self, champion: str, action: str, timestamp: float
    ) -> None:
        """Record a timed action.

        Args:
            champion: Champion/player name.
            action: Action type string (e.g., "spell_cast", "q_cast").
            timestamp: Game time when action occurred.
        """
        self._actions[champion].append({
            "action": action,
            "timestamp": timestamp,
        })

    def get_actions(self, champion: str) -> list[dict[str, Any]]:
        """Get all recorded actions for a champion.

        Args:
            champion: Champion name.

        Returns:
            List of action dicts sorted by timestamp.
        """
        return list(self._actions.get(champion, []))

    def calculate_reaction_time(
        self, champion: str, stimulus: str, response: str
    ) -> float:
        """Calculate reaction time between stimulus and response.

        Finds the first matching stimulus-response pair.

        Args:
            champion: Champion name.
            stimulus: Stimulus action type.
            response: Response action type.

        Returns:
            Reaction time in seconds, or -1.0 if not found.
        """
        actions = self._actions.get(champion, [])
        stim_time: Optional[float] = None
        for a in actions:
            if a["action"] == stimulus and stim_time is None:
                stim_time = a["timestamp"]
            elif a["action"] == response and stim_time is not None:
                return a["timestamp"] - stim_time
        return -1.0

    def average_reaction_time(self, champion: str) -> float:
        """Calculate average reaction time across all stimulus-response pairs.

        Pairs consecutive actions as stimulus→response.

        Args:
            champion: Champion name.

        Returns:
            Average reaction time in seconds.
        """
        actions = self._actions.get(champion, [])
        if len(actions) < 2:
            return 0.0

        # Group by pairs of (stimulus, response) based on alternating pattern
        stimuli = [a for a in actions if a["action"] == "stimulus"]
        responses = [a for a in actions if a["action"] == "response"]

        if not stimuli or not responses:
            # Fallback: pair consecutive different actions
            times: list[float] = []
            for i in range(0, len(actions) - 1, 2):
                dt = actions[i + 1]["timestamp"] - actions[i]["timestamp"]
                times.append(dt)
            return sum(times) / len(times) if times else 0.0

        times = []
        for s, r in zip(stimuli, responses):
            times.append(r["timestamp"] - s["timestamp"])
        return sum(times) / len(times) if times else 0.0

    def estimate_cooldown(self, champion: str, action: str) -> float:
        """Estimate ability cooldown from consecutive uses.

        Args:
            champion: Champion name.
            action: Action type (e.g., "q_cast").

        Returns:
            Estimated cooldown in seconds, or 0.0 if insufficient data.
        """
        actions = self._actions.get(champion, [])
        uses = [a["timestamp"] for a in actions if a["action"] == action]
        if len(uses) < 2:
            return 0.0
        intervals = [uses[i + 1] - uses[i] for i in range(len(uses) - 1)]
        return sum(intervals) / len(intervals)

    def action_frequency(self, champion: str, action: str) -> float:
        """Compute action frequency (actions per second).

        Args:
            champion: Champion name.
            action: Action type.

        Returns:
            Frequency in actions/second.
        """
        actions = self._actions.get(champion, [])
        uses = [a["timestamp"] for a in actions if a["action"] == action]
        if len(uses) < 2:
            return 0.0
        duration = uses[-1] - uses[0]
        if duration <= 0:
            return 0.0
        return (len(uses) - 1) / duration

    def detect_timing_pattern(
        self, champion: str, action: str
    ) -> Optional[dict[str, Any]]:
        """Detect regular timing patterns for an action.

        Args:
            champion: Champion name.
            action: Action type.

        Returns:
            Pattern dict with interval and regularity, or None.
        """
        actions = self._actions.get(champion, [])
        uses = [a["timestamp"] for a in actions if a["action"] == action]
        if len(uses) < 3:
            return None

        intervals = [uses[i + 1] - uses[i] for i in range(len(uses) - 1)]
        avg_interval = sum(intervals) / len(intervals)
        variance = sum((i - avg_interval) ** 2 for i in intervals) / len(intervals)
        std_dev = variance ** 0.5
        regularity = 1.0 - min(std_dev / max(avg_interval, 0.01), 1.0)

        return {
            "interval": avg_interval,
            "regularity": regularity,
            "samples": len(uses),
        }

    def reset(self) -> None:
        """Reset all tracked actions."""
        self._actions.clear()

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "opponent_timing_tracker",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
