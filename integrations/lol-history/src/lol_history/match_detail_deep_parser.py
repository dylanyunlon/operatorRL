"""
MatchDetailDeepParser — Deep parser for individual match detail responses.

Architecture (拿来主义):
  查看 **integrations/lol-history/src/lol_history/game_detail_parser.py** 上现有
  **participant stats字段提取和KDA计算方式** 的实现方式，理解其模式。
  从 **match_analyzer.py** 开始——它展示了raw match → structured stats的转换。
  遵循该模式实现 **MatchDetailDeepParser**，让 **seraphine_deep_history_pipeline（M604）**
  可以 **将API原始响应解析为标准化的participant/metadata/items结构**。

Location: integrations/lol-history/src/lol_history/match_detail_deep_parser.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.match_detail_deep_parser.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    return (k + a) / max(d, 1)


class MatchDetailDeepParser:
    """Deep parser for match detail API responses.

    Public API
    ----------
    parse_participant_stats(raw) -> dict
    parse_game_metadata(raw) -> dict
    parse_item_build(raw) -> list[int]
    extract_all_participants(game) -> list[dict]
    compute_team_totals(participants) -> dict
    deep_parse(game) -> dict
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._parse_count: int = 0

    def parse_participant_stats(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a single participant's stats from raw data."""
        k = raw.get("kills", 0)
        d = raw.get("deaths", 0)
        a = raw.get("assists", 0)
        cs = raw.get("totalMinionsKilled", 0)
        gold = raw.get("goldEarned", 0)
        level = raw.get("champLevel", 0)
        wards = raw.get("wardsPlaced", 0)

        kda_val = _kda(k, d, a) if (k or d or a) else 0.0

        self._parse_count += 1
        result = {
            "kills": k,
            "deaths": d,
            "assists": a,
            "kda": kda_val,
            "cs": cs,
            "gold": gold,
            "level": level,
            "wards_placed": wards,
        }
        self._fire("participant_parsed", {"parse_count": self._parse_count})
        return result

    def parse_game_metadata(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Parse top-level game metadata."""
        duration_sec = raw.get("gameDuration", 0)
        return {
            "game_id": raw.get("gameId", 0),
            "duration_seconds": duration_sec,
            "duration_minutes": _safe_div(duration_sec, 60),
            "game_mode": raw.get("gameMode", "UNKNOWN"),
            "map_id": raw.get("mapId", 0),
            "creation_timestamp": raw.get("gameCreation", 0),
        }

    def parse_item_build(self, raw: Dict[str, Any]) -> List[int]:
        """Extract non-zero item IDs from participant data."""
        items = []
        for i in range(7):
            item_id = raw.get(f"item{i}", 0)
            if item_id and item_id != 0:
                items.append(item_id)
        return items

    def extract_all_participants(self, game: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract and parse all participants from a game."""
        participants = game.get("participants", [])
        return [self.parse_participant_stats(p) for p in participants]

    def compute_team_totals(self, participants: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregate totals for a team."""
        total_kills = sum(p.get("kills", 0) for p in participants)
        total_deaths = sum(p.get("deaths", 0) for p in participants)
        total_assists = sum(p.get("assists", 0) for p in participants)
        total_gold = sum(p.get("gold", 0) for p in participants)
        return {
            "total_kills": total_kills,
            "total_deaths": total_deaths,
            "total_assists": total_assists,
            "total_gold": total_gold,
            "team_kda": _kda(total_kills, total_deaths, total_assists),
        }

    def deep_parse(self, game: Dict[str, Any]) -> Dict[str, Any]:
        """Full deep parse of a game response."""
        metadata = self.parse_game_metadata(game)
        participants = self.extract_all_participants(game)
        return {
            "metadata": metadata,
            "participants": participants,
            "participant_count": len(participants),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback:
            self.evolution_callback({
                "type": event_type,
                "key": _EVOLUTION_KEY,
                "timestamp": time.time(),
                **data,
            })
