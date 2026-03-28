"""
Replay Analyzer — Dota 2 .dem file analysis and training annotation.

Provides replay parsing, kill/teamfight extraction, gold advantage
timelines, and training data annotation. Adapted from DI-star's
replay_decoder.py patterns for game replay processing.

Location: integrations/dota2/src/dota2_agent/replay_analyzer.py

Reference: DI-star/distar/agent/default/replay_decoder.py.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "dota2_agent.replay_analyzer.v1"


class ReplayAnalyzer:
    """Dota 2 replay (.dem) analyzer for training data extraction.

    Mirrors DI-star's replay decoder pipeline:
    1. Parse raw replay → events
    2. Extract kills, teamfights, gold timelines
    3. Annotate for training (state/action/reward spans)
    """

    def __init__(self, replay_dir: str = "/tmp/dota2_replays") -> None:
        self.replay_dir = replay_dir
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_replay(self, filepath: str) -> dict[str, Any]:
        """Parse a .dem replay file.

        Without a real .dem parser, returns empty structure.
        In production, delegates to clarity or Valve's demoinfogo.

        Args:
            filepath: Path to .dem file.

        Returns:
            Dict with events list.
        """
        if not os.path.isfile(filepath):
            logger.warning("Replay file not found: %s", filepath)
            return {"events": [], "filepath": filepath}

        # Stub: real implementation would parse binary .dem
        return {"events": [], "filepath": filepath}

    def extract_kills(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract kill events from event stream.

        Args:
            events: List of game events.

        Returns:
            Filtered list containing only kill events.
        """
        return [e for e in events if e.get("type") == "kill"]

    def compute_gold_advantage(
        self, snapshots: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Compute Radiant gold advantage timeline.

        Args:
            snapshots: Time-series of {time, radiant_gold, dire_gold}.

        Returns:
            List of {time, advantage} dicts (positive = Radiant ahead).
        """
        timeline = []
        for snap in snapshots:
            advantage = snap.get("radiant_gold", 0) - snap.get("dire_gold", 0)
            timeline.append({
                "time": snap.get("time", 0),
                "advantage": advantage,
            })
        return timeline

    def annotate_for_training(
        self,
        events: list[dict[str, Any]],
        game_result: str = "unknown",
    ) -> list[dict[str, Any]]:
        """Convert events into training spans (state/action/reward).

        Mirrors DI-star's replay → training data pipeline.

        Args:
            events: Game event list.
            game_result: "win", "loss", or "unknown".

        Returns:
            List of training span dicts.
        """
        reward_base = 1.0 if game_result == "win" else (-1.0 if game_result == "loss" else 0.0)
        spans = []
        for i, event in enumerate(events):
            spans.append({
                "state": event,
                "action": event.get("type", "unknown"),
                "reward": reward_base * (0.5 + 0.5 * (i / max(len(events), 1))),
                "game_result": game_result,
                "event_index": i,
            })

        self._fire_evolution({
            "event": "annotated_replay",
            "span_count": len(spans),
            "game_result": game_result,
        })
        return spans

    def list_replays(self) -> list[str]:
        """List available .dem replay files.

        Returns:
            List of file paths.
        """
        if not os.path.isdir(self.replay_dir):
            return []
        return [
            os.path.join(self.replay_dir, f)
            for f in os.listdir(self.replay_dir)
            if f.endswith(".dem")
        ]

    def detect_teamfights(
        self,
        events: list[dict[str, Any]],
        window: float = 15.0,
    ) -> list[dict[str, Any]]:
        """Detect teamfight clusters from kill events.

        Groups kills within `window` seconds of each other.

        Args:
            events: List of game events.
            window: Time window in seconds.

        Returns:
            List of teamfight dicts with start_time, end_time, kill_count.
        """
        kills = self.extract_kills(events)
        if not kills:
            return []

        kills_sorted = sorted(kills, key=lambda k: k.get("time", 0))
        fights = []
        current_fight = [kills_sorted[0]]

        for kill in kills_sorted[1:]:
            if kill.get("time", 0) - current_fight[-1].get("time", 0) <= window:
                current_fight.append(kill)
            else:
                if len(current_fight) >= 3:
                    fights.append({
                        "start_time": current_fight[0].get("time", 0),
                        "end_time": current_fight[-1].get("time", 0),
                        "kill_count": len(current_fight),
                        "kills": current_fight,
                    })
                current_fight = [kill]

        # Check last cluster
        if len(current_fight) >= 3:
            fights.append({
                "start_time": current_fight[0].get("time", 0),
                "end_time": current_fight[-1].get("time", 0),
                "kill_count": len(current_fight),
                "kills": current_fight,
            })

        return fights

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
