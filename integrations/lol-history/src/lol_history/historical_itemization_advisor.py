"""
HistoricalItemizationAdvisor — Recommends item builds from historical matchup data.

Architecture (拿来主义):
  item_build_path_analyzer.py — item build path analysis patterns
  opgg_build_fetcher.py — external build data fetching

Location: integrations/lol-history/src/lol_history/historical_itemization_advisor.py

Design Notes (Knuth-level critique):
  User:
    - advise() returns ranked item builds with win rates for the specific matchup.
    - Distinguishes core items from situational items.
  System:
    - Matchup-specific builds override generic builds when sample size is sufficient.
    - Item timestamps enable power spike detection integration (M720).
"""
from __future__ import annotations
import logging, time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.historical_itemization_advisor.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class HistoricalItemizationAdvisor:
    """Advises item builds from historical matchup data.

    Public API: ingest_build_data, advise, advise_next_item, get_stats
    """
    def __init__(self, min_samples: int = 5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._min_samples = min_samples
        # key: (our_champ, enemy_champ, role) -> list of build records
        self._matchup_builds: Dict[Tuple, List[Dict]] = defaultdict(list)
        # key: (our_champ, role) -> list of build records (generic)
        self._generic_builds: Dict[Tuple, List[Dict]] = defaultdict(list)
        self._advise_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def ingest_build_data(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ingest historical build records.

        Each record: {our_champ, enemy_champ, role, items: [item_ids in order],
                      win: bool, game_duration: int, item_timestamps: [optional]}
        """
        self._op_count += 1
        ingested = 0
        for rec in records:
            our = rec.get("our_champ", 0)
            enemy = rec.get("enemy_champ", 0)
            role = rec.get("role", "any")
            if not our or not rec.get("items"):
                continue
            self._matchup_builds[(our, enemy, role)].append(rec)
            self._generic_builds[(our, role)].append(rec)
            ingested += 1
        return {"status": "ok", "ingested": ingested, "matchup_keys": len(self._matchup_builds)}

    def advise(self, our_champ: int, enemy_champ: int, role: str = "any",
               current_items: List[int] = None, max_builds: int = 3) -> Dict[str, Any]:
        """Recommend item builds for a specific matchup.

        Args:
            our_champ: Our champion ID.
            enemy_champ: Lane opponent champion ID.
            role: Lane role.
            current_items: Items already purchased.
            max_builds: Max number of build paths to suggest.
        """
        self._op_count += 1
        self._advise_count += 1
        current_items = set(current_items or [])

        # Try matchup-specific first
        key = (our_champ, enemy_champ, role)
        records = self._matchup_builds.get(key, [])
        source = "matchup_specific"

        if len(records) < self._min_samples:
            # Fallback to generic
            records = self._generic_builds.get((our_champ, role), [])
            source = "generic"

        if not records:
            return {"status": "ok", "builds": [], "source": "none", "note": "no_data"}

        # Cluster builds by first 3 items (core build path)
        core_paths: Dict[Tuple, List[Dict]] = defaultdict(list)
        for rec in records:
            items = rec.get("items", [])
            core = tuple(items[:3]) if len(items) >= 3 else tuple(items)
            core_paths[core].append(rec)

        # Score each core path
        scored_builds = []
        for core, recs in core_paths.items():
            n = len(recs)
            if n < 2:
                continue
            wins = sum(1 for r in recs if r.get("win"))
            wr = _safe_div(wins, n)
            avg_duration = _safe_div(sum(r.get("game_duration", 0) for r in recs), n)

            # Full build: most common item sequence from this core
            full_items_counter = Counter()
            for r in recs:
                for item_id in r.get("items", [])[3:6]:
                    full_items_counter[item_id] += 1
            situational = [item_id for item_id, _ in full_items_counter.most_common(3)]

            scored_builds.append({
                "core_items": list(core),
                "situational_items": situational,
                "win_rate": round(wr, 4),
                "sample_size": n,
                "avg_game_duration": round(avg_duration),
                "source": source,
            })

        scored_builds.sort(key=lambda x: (x["win_rate"], x["sample_size"]), reverse=True)

        result = {
            "status": "ok",
            "builds": scored_builds[:max_builds],
            "source": source,
            "total_records": len(records),
            "our_champ": our_champ, "enemy_champ": enemy_champ,
        }
        self._fire("advised", {"our_champ": our_champ, "builds": len(scored_builds)})
        return result

    def advise_next_item(self, our_champ: int, enemy_champ: int, role: str,
                         current_items: List[int]) -> Dict[str, Any]:
        """Recommend the next single item to buy.

        Args:
            current_items: Items already purchased (in order).
        """
        self._op_count += 1
        slot = len(current_items)

        key = (our_champ, enemy_champ, role)
        records = self._matchup_builds.get(key, [])
        if len(records) < self._min_samples:
            records = self._generic_builds.get((our_champ, role), [])

        if not records:
            return {"status": "ok", "next_item": None, "note": "no_data"}

        # Find most common item at this slot position
        next_counter = Counter()
        next_wins = defaultdict(int)
        next_total = defaultdict(int)
        for rec in records:
            items = rec.get("items", [])
            if len(items) > slot:
                nxt = items[slot]
                if nxt not in set(current_items):
                    next_counter[nxt] += 1
                    next_total[nxt] += 1
                    if rec.get("win"):
                        next_wins[nxt] += 1

        if not next_counter:
            return {"status": "ok", "next_item": None, "note": "no_candidates"}

        candidates = []
        for item_id, count in next_counter.most_common(5):
            wr = _safe_div(next_wins[item_id], next_total[item_id])
            candidates.append({"item_id": item_id, "frequency": count,
                               "win_rate": round(wr, 4), "sample_size": next_total[item_id]})

        return {"status": "ok", "next_item": candidates[0]["item_id"],
                "candidates": candidates, "slot": slot}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "advise_count": self._advise_count,
                "matchup_keys": len(self._matchup_builds),
                "generic_keys": len(self._generic_builds),
                "total_records": sum(len(v) for v in self._matchup_builds.values())}
