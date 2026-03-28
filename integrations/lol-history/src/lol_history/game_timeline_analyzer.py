"""
Game Timeline Analyzer — Key turning point detection and replay annotation.

Detects gold swings, team fights, objective takes, and game phase
transitions from timeline data. Annotates turning points for replay review.

Location: integrations/lol-history/src/lol_history/game_timeline_analyzer.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.game_timeline_analyzer.v1"


class GameTimelineAnalyzer:
    """Analyze game timelines for turning points and key events.

    Detects gold swings, team fights, objectives, and produces
    annotated turning point labels for replay review.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def detect_gold_swings(
        self,
        frames: list[dict[str, Any]],
        threshold: int = 2000,
    ) -> list[dict[str, Any]]:
        """Detect significant gold swings between consecutive frames.

        Args:
            frames: List of frame dicts with timestamp and team_gold_diff.
            threshold: Minimum absolute change to qualify as a swing.

        Returns:
            List of swing event dicts.
        """
        if not frames or len(frames) < 2:
            return []

        swings: list[dict[str, Any]] = []
        for i in range(1, len(frames)):
            prev_diff = frames[i - 1].get("team_gold_diff", 0)
            curr_diff = frames[i].get("team_gold_diff", 0)
            change = abs(curr_diff - prev_diff)
            if change >= threshold:
                swings.append({
                    "timestamp": frames[i].get("timestamp", 0),
                    "type": "gold_swing",
                    "magnitude": change,
                    "from_diff": prev_diff,
                    "to_diff": curr_diff,
                })
        return swings

    def detect_team_fights(
        self,
        events: list[dict[str, Any]],
        time_window_ms: int = 5000,
    ) -> list[dict[str, Any]]:
        """Detect team fights from kill events clustered in time.

        Args:
            events: List of event dicts with type and timestamp.
            time_window_ms: Maximum time window for kills to be in same fight.

        Returns:
            List of team fight event dicts.
        """
        kills = [e for e in events if e.get("type") == "CHAMPION_KILL"]
        if not kills:
            return []

        kills.sort(key=lambda e: e.get("timestamp", 0))
        fights: list[dict[str, Any]] = []
        cluster: list[dict] = [kills[0]]

        for i in range(1, len(kills)):
            if kills[i]["timestamp"] - cluster[0]["timestamp"] <= time_window_ms:
                cluster.append(kills[i])
            else:
                if len(cluster) >= 2:
                    fights.append({
                        "type": "team_fight",
                        "timestamp": cluster[0]["timestamp"],
                        "kills": len(cluster),
                        "duration_ms": cluster[-1]["timestamp"] - cluster[0]["timestamp"],
                    })
                cluster = [kills[i]]

        if len(cluster) >= 2:
            fights.append({
                "type": "team_fight",
                "timestamp": cluster[0]["timestamp"],
                "kills": len(cluster),
                "duration_ms": cluster[-1]["timestamp"] - cluster[0]["timestamp"],
            })

        return fights

    def detect_objectives(
        self, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Detect objective takes (dragons, barons, towers).

        Args:
            events: List of event dicts.

        Returns:
            List of objective event dicts.
        """
        obj_types = {"ELITE_MONSTER_KILL", "BUILDING_KILL"}
        return [
            {
                "type": e.get("type", ""),
                "timestamp": e.get("timestamp", 0),
                "monster_type": e.get("monsterType", ""),
                "building_type": e.get("buildingType", ""),
            }
            for e in events
            if e.get("type") in obj_types
        ]

    def annotate_turning_points(
        self, turning_points: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Annotate turning points with human-readable labels.

        Args:
            turning_points: List of turning point dicts with type and magnitude.

        Returns:
            List of annotated dicts with label and timestamp.
        """
        annotations: list[dict[str, Any]] = []
        for tp in turning_points:
            tp_type = tp.get("type", "unknown")
            magnitude = tp.get("magnitude", 0)
            label = f"{tp_type} (magnitude: {magnitude})"
            annotations.append({
                "timestamp": tp.get("timestamp", 0),
                "type": tp_type,
                "magnitude": magnitude,
                "label": label,
            })
        return annotations

    def detect_game_phase(self, timestamp_ms: int) -> str:
        """Detect game phase from timestamp.

        Args:
            timestamp_ms: Game time in milliseconds.

        Returns:
            "early" (0-15min), "mid" (15-30min), or "late" (30min+).
        """
        minutes = timestamp_ms / 60000.0
        if minutes < 15:
            return "early"
        elif minutes < 30:
            return "mid"
        else:
            return "late"

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "game_timeline_analyzer",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
