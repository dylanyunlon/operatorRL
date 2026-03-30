"""
ItemBuildPathAnalyzer — Analyze item build paths and winrates.

Architecture (拿来主义):
  查看 **opgg_build_fetcher.py** 的出装数据解析方式。从 **matchup_database.py** 的
  record → query模式开始。实现 **ItemBuildPathAnalyzer**。

Location: integrations/lol-history/src/lol_history/item_build_path_analyzer.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.item_build_path_analyzer.v1"


class ItemBuildPathAnalyzer:
    """Analyze item build paths per champion."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        # champion -> list of (items_tuple, won)
        self._builds: Dict[str, List[Tuple[Tuple[int, ...], bool]]] = defaultdict(list)

    def record_build(self, champion: str, items: List[int], won: bool) -> None:
        self._builds[champion].append((tuple(items), won))
        self._fire("build_recorded", {"champion": champion, "items": items, "won": won})

    def get_champion_builds(self, champion: str) -> List[Dict[str, Any]]:
        if champion not in self._builds:
            return []
        counter: Dict[Tuple[int, ...], Dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0})
        for items, won in self._builds[champion]:
            counter[items]["games"] += 1
            if won:
                counter[items]["wins"] += 1
        return [
            {"items": list(k), "games": v["games"], "wins": v["wins"],
             "winrate": v["wins"] / v["games"]}
            for k, v in counter.items()
        ]

    def get_most_common_build(self, champion: str) -> Dict[str, Any]:
        builds = self.get_champion_builds(champion)
        if not builds:
            return {"items": [], "games": 0, "winrate": 0.0}
        builds.sort(key=lambda x: x["games"], reverse=True)
        return builds[0]

    def get_build_winrate(self, champion: str, items: List[int]) -> float:
        key = tuple(items)
        wins = games = 0
        for it, won in self._builds.get(champion, []):
            if it == key:
                games += 1
                if won:
                    wins += 1
        return wins / games if games else 0.0

    def get_first_item_stats(self, champion: str) -> Dict[int, Dict[str, Any]]:
        result: Dict[int, Dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0})
        for items, won in self._builds.get(champion, []):
            if items:
                first = items[0]
                result[first]["games"] += 1
                if won:
                    result[first]["wins"] += 1
        return {k: {**v, "winrate": v["wins"] / v["games"] if v["games"] else 0.0}
                for k, v in result.items()}

    def get_item_frequency(self, champion: str) -> Dict[int, int]:
        freq: Dict[int, int] = defaultdict(int)
        for items, _ in self._builds.get(champion, []):
            for item in items:
                freq[item] += 1
        return dict(freq)

    def to_dict(self) -> Dict[str, Any]:
        return {c: self.get_champion_builds(c) for c in self._builds}

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback:
            self.evolution_callback({
                "type": event_type, "key": _EVOLUTION_KEY,
                "timestamp": time.time(), **data,
            })
