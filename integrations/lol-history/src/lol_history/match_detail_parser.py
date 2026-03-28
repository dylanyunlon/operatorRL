"""
Match Detail Parser — JSON to structured data + timeline extraction.

Parses Riot match JSON into structured participant data, timeline events,
gold tracking, and KDA calculations.

Location: integrations/lol-history/src/lol_history/match_detail_parser.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.match_detail_parser.v1"


class MatchDetailParser:
    """Parser for Riot Games match detail JSON.

    Extracts participants, timeline events, gold data, KDA,
    and game duration from raw match JSON responses.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_participants(
        self, raw: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Parse participant data from match JSON.

        Args:
            raw: Raw match JSON with info.participants.

        Returns:
            List of normalized participant dicts.
        """
        info = raw.get("info", {})
        participants = info.get("participants", [])
        result: list[dict[str, Any]] = []
        for p in participants:
            result.append({
                "puuid": p.get("puuid", ""),
                "champion": p.get("championName", ""),
                "win": p.get("win", False),
                "kills": p.get("kills", 0),
                "deaths": p.get("deaths", 0),
                "assists": p.get("assists", 0),
            })
        return result

    def extract_timeline_events(
        self, timeline: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Extract all events from timeline frames.

        Args:
            timeline: Raw timeline JSON with info.frames.

        Returns:
            Flat list of events with timestamps.
        """
        info = timeline.get("info", {})
        frames = info.get("frames", [])
        events: list[dict[str, Any]] = []
        for frame in frames:
            for event in frame.get("events", []):
                events.append(event)
        return events

    def extract_gold_at_timestamps(
        self,
        timeline: dict[str, Any],
        participant_id: str,
    ) -> list[dict[str, Any]]:
        """Extract gold values at each timestamp for a participant.

        Args:
            timeline: Raw timeline JSON.
            participant_id: Participant ID string.

        Returns:
            List of {timestamp, gold} dicts.
        """
        info = timeline.get("info", {})
        frames = info.get("frames", [])
        gold_data: list[dict[str, Any]] = []
        for frame in frames:
            ts = frame.get("timestamp", 0)
            pframes = frame.get("participantFrames", {})
            pf = pframes.get(participant_id, {})
            gold_data.append({
                "timestamp": ts,
                "gold": pf.get("totalGold", 0),
            })
        return gold_data

    def calculate_kda(
        self, kills: int, deaths: int, assists: int
    ) -> float:
        """Calculate KDA ratio.

        Args:
            kills: Kill count.
            deaths: Death count.
            assists: Assist count.

        Returns:
            KDA value. Returns inf or (kills+assists)*perfect_multiplier if 0 deaths.
        """
        if deaths == 0:
            return float("inf")
        return (kills + assists) / deaths

    def extract_game_duration(self, raw: dict[str, Any]) -> int:
        """Extract game duration in seconds.

        Args:
            raw: Raw match JSON.

        Returns:
            Duration in seconds.
        """
        info = raw.get("info", {})
        return info.get("gameDuration", 0)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "match_detail_parser",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
