"""
CrossMatchPatternDetector — Detects recurring patterns across multiple opponent matches.

Architecture (拿来主义):
  game_event_pattern_library.py（M615）— pattern store/query
  comeback_pattern_detector.py — pattern detection logic

Location: integrations/lol-history/src/lol_history/cross_match_pattern_detector.py

Design Notes (Knuth-level critique):
  User:
    - detect() finds recurring behaviors (always invades at 1:30, tends to roam at 6min, etc.)
    - Patterns are ranked by consistency (frequency across matches).
  System:
    - Uses time-binned event frequency analysis to find outlier behaviors.
    - Pattern confidence scales with number of matches exhibiting the pattern.
"""
from __future__ import annotations
import logging, time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.cross_match_pattern_detector.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class CrossMatchPatternDetector:
    """Detects recurring behavioral patterns across multiple opponent matches.

    Public API: detect, detect_timing_patterns, detect_behavior_habits, get_stats
    """
    def __init__(self, min_frequency: float = 0.5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._min_frequency = min_frequency
        self._detect_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def detect(self, matches: List[Dict[str, Any]], puuid: str = "") -> Dict[str, Any]:
        """Detect all recurring patterns across matches.

        Args:
            matches: List of match dicts with events, timeline data.

        Returns:
            Dict with timing_patterns, behavior_habits, and strategic_tendencies.
        """
        self._op_count += 1
        self._detect_count += 1
        t0 = time.time()

        timing = self.detect_timing_patterns(matches)
        behavior = self.detect_behavior_habits(matches)
        strategic = self._detect_strategic_tendencies(matches)

        all_patterns = (timing.get("patterns", []) + behavior.get("habits", []) +
                        strategic.get("tendencies", []))
        all_patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)

        elapsed = round((time.time() - t0) * 1000, 1)
        result = {
            "status": "ok", "puuid": puuid,
            "patterns": all_patterns,
            "pattern_count": len(all_patterns),
            "timing_patterns": timing.get("patterns", []),
            "behavior_habits": behavior.get("habits", []),
            "strategic_tendencies": strategic.get("tendencies", []),
            "matches_analyzed": len(matches),
            "elapsed_ms": elapsed,
        }
        self._fire("detected", {"puuid": puuid, "patterns": len(all_patterns)})
        return result

    def detect_timing_patterns(self, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect timing-based patterns (e.g. always backs at 5min, first gank at 3min).

        Looks at events with game_time and finds consistent timing clusters.
        """
        self._op_count += 1
        if not matches:
            return {"patterns": []}

        # Collect event timings across matches
        event_timings: Dict[str, List[float]] = defaultdict(list)
        for m in matches:
            events = m.get("events", m.get("timeline", {}).get("events", []))
            if not isinstance(events, list):
                continue
            for ev in events:
                et = ev.get("event_type", ev.get("type", ""))
                gt = ev.get("game_time", ev.get("timestamp", 0))
                if et and gt > 0:
                    event_timings[et].append(gt)

        patterns = []
        n_matches = len(matches)
        for event_type, timings in event_timings.items():
            if len(timings) < 2:
                continue
            # Bin into 60-second windows
            bins: Dict[int, int] = Counter()
            for t in timings:
                bin_key = int(t // 60)
                bins[bin_key] += 1

            for minute, count in bins.most_common(3):
                frequency = _safe_div(count, n_matches)
                if frequency >= self._min_frequency:
                    patterns.append({
                        "type": "timing",
                        "event": event_type,
                        "typical_time_min": minute,
                        "typical_time_range": f"{minute}:00-{minute}:59",
                        "frequency": round(frequency, 4),
                        "occurrences": count,
                        "confidence": round(min(frequency, 1.0), 4),
                    })

        patterns.sort(key=lambda p: p["confidence"], reverse=True)
        return {"patterns": patterns}

    def detect_behavior_habits(self, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect behavioral habits (e.g. always starts same item, prefers aggressive plays)."""
        self._op_count += 1
        if not matches:
            return {"habits": []}

        habits = []
        n = len(matches)

        # Starting item consistency
        start_items = Counter()
        for m in matches:
            si = m.get("starting_item", m.get("item0", 0))
            if si:
                start_items[si] += 1
        for item_id, count in start_items.most_common(1):
            freq = _safe_div(count, n)
            if freq >= self._min_frequency:
                habits.append({
                    "type": "behavior", "habit": "consistent_start_item",
                    "item_id": item_id, "frequency": round(freq, 4),
                    "confidence": round(min(freq, 1.0), 4),
                })

        # Summoner spell consistency
        spell_combos = Counter()
        for m in matches:
            s1 = m.get("summoner1Id", m.get("spell1", 0))
            s2 = m.get("summoner2Id", m.get("spell2", 0))
            if s1 and s2:
                combo = tuple(sorted([s1, s2]))
                spell_combos[combo] += 1
        for combo, count in spell_combos.most_common(1):
            freq = _safe_div(count, n)
            if freq >= 0.7:
                habits.append({
                    "type": "behavior", "habit": "consistent_summoner_spells",
                    "spells": list(combo), "frequency": round(freq, 4),
                    "confidence": round(min(freq, 1.0), 4),
                })

        # Aggression level
        avg_kills = _safe_div(sum(m.get("kills", 0) for m in matches), n)
        avg_deaths = _safe_div(sum(m.get("deaths", 0) for m in matches), n)
        if avg_kills > 0 or avg_deaths > 0:
            if avg_kills / max(avg_deaths, 1) > 2.0:
                habits.append({"type": "behavior", "habit": "aggressive_player",
                               "kd_ratio": round(avg_kills / max(avg_deaths, 1), 2),
                               "confidence": 0.7})
            elif avg_deaths > avg_kills * 1.5:
                habits.append({"type": "behavior", "habit": "passive_or_reckless",
                               "kd_ratio": round(avg_kills / max(avg_deaths, 1), 2),
                               "confidence": 0.6})

        return {"habits": habits}

    def _detect_strategic_tendencies(self, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Detect strategic tendencies (split push preference, teamfight focus, etc.)."""
        n = len(matches)
        if not n:
            return {"tendencies": []}

        tendencies = []

        # CS focus
        avg_cs = _safe_div(sum(m.get("cs", m.get("totalMinionsKilled", 0)) for m in matches), n)
        if avg_cs > 200:
            tendencies.append({"type": "strategic", "tendency": "farm_focused",
                               "avg_cs": round(avg_cs, 1), "confidence": 0.7})
        elif avg_cs < 100:
            tendencies.append({"type": "strategic", "tendency": "fight_focused",
                               "avg_cs": round(avg_cs, 1), "confidence": 0.6})

        # Vision focus
        avg_vision = _safe_div(sum(m.get("visionScore", 0) for m in matches), n)
        if avg_vision > 30:
            tendencies.append({"type": "strategic", "tendency": "vision_oriented",
                               "avg_vision": round(avg_vision, 1), "confidence": 0.7})

        # Objective participation
        avg_obj = _safe_div(
            sum(m.get("objectivesStolen", 0) + m.get("dragonKills", 0)
                + m.get("baronKills", 0) for m in matches), n)
        if avg_obj > 1.5:
            tendencies.append({"type": "strategic", "tendency": "objective_focused",
                               "avg_objectives": round(avg_obj, 1), "confidence": 0.6})

        return {"tendencies": tendencies}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "detect_count": self._detect_count,
                "min_frequency": self._min_frequency}
