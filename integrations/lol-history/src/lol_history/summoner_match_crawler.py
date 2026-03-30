"""
SummonerMatchCrawler — Paginated match history crawler for a summoner.

Architecture (拿来主义):
  查看 **integrations/lol-history/src/lol_history/seraphine_lcu_deep_client.py** 上现有
  **LCU endpoint URL构建和分页请求方式** 的实现方式，理解其模式，特别是build_url如何
  与parse_response分离。可以从 **seraphine_bridge.py** 这个好例子开始——它的
  build_match_history_url展示了puuid+begIndex+endIndex的分页URL构建。然后，遵循该
  模式实现一个新的 **SummonerMatchCrawler**，让 **seraphine_deep_history_pipeline（M604）**
  可以 **系统性地爬取任意召唤师的全量战绩**，并能 **去重、追踪爬取进度、通过evolution_callback
  上报爬取事件**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

Location: integrations/lol-history/src/lol_history/summoner_match_crawler.py

Design Notes (Knuth-level critique):
  User:
    - Pagination plan is computed upfront so caller knows total work.
    - Deduplication ensures idempotent crawls across overlapping pages.
    - Crawl status tracking lets UI show progress bar.
  System:
    - Page size is configurable for different API rate-limit budgets.
    - parse_match_list_response tolerates missing/malformed responses.
    - Evolution events fire on plan creation for training pipeline awareness.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.summoner_match_crawler.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class SummonerMatchCrawler:
    """Paginated match history crawler for a summoner.

    Public API
    ----------
    build_crawl_plan(puuid, count, page_size) -> dict
    parse_match_list_response(response) -> list[dict]
    deduplicate_matches(matches) -> list[dict]
    get_crawl_status() -> dict
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._completed_pages: int = 0
        self._total_pages: int = 0
        self._all_game_ids: set = set()
        self._crawled_matches: List[Dict[str, Any]] = []

    def build_crawl_plan(
        self,
        puuid: str,
        count: int,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Build a pagination plan for crawling match history.

        Parameters
        ----------
        puuid : str
            Player unique ID.
        count : int
            Total number of matches to crawl.
        page_size : int
            Matches per page (default 20).

        Returns
        -------
        dict with total_pages, pages list, puuid, count.
        """
        if count <= 0 or page_size <= 0:
            self._total_pages = 0
            plan = {"total_pages": 0, "pages": [], "puuid": puuid, "count": count}
            self._fire("crawl_plan_created", plan)
            return plan

        total_pages = math.ceil(count / page_size)
        pages = []
        for i in range(total_pages):
            start = i * page_size
            end = min(start + page_size, count)
            pages.append({"page": i, "start": start, "end": end, "size": end - start})

        self._total_pages = total_pages
        self._completed_pages = 0

        plan = {
            "total_pages": total_pages,
            "pages": pages,
            "puuid": puuid,
            "count": count,
            "page_size": page_size,
        }
        self._fire("crawl_plan_created", plan)
        return plan

    def parse_match_list_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse a match list API response.

        Tolerates both nested and flat structures.

        Parameters
        ----------
        response : dict
            Raw API response.

        Returns
        -------
        list of match dicts.
        """
        games = response.get("games", {})
        if isinstance(games, dict):
            match_list = games.get("games", [])
        elif isinstance(games, list):
            match_list = games
        else:
            match_list = []

        if not match_list and "matches" in response:
            match_list = response["matches"]

        self._completed_pages += 1
        return list(match_list)

    def deduplicate_matches(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate matches by gameId.

        Parameters
        ----------
        matches : list[dict]
            List of match dicts with gameId field.

        Returns
        -------
        Deduplicated list preserving first occurrence order.
        """
        seen: set = set()
        result: List[Dict[str, Any]] = []
        for m in matches:
            gid = m.get("gameId")
            if gid is not None and gid not in seen:
                seen.add(gid)
                result.append(m)
            elif gid is None:
                result.append(m)
        return result

    def get_crawl_status(self) -> Dict[str, Any]:
        """Return current crawl progress."""
        return {
            "completed_pages": self._completed_pages,
            "total_pages": self._total_pages,
            "progress": _safe_div(self._completed_pages, self._total_pages),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback:
            self.evolution_callback({
                "type": event_type,
                "key": _EVOLUTION_KEY,
                "timestamp": time.time(),
                **data,
            })
