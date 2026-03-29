"""
Opponent Weakness Detector — Identify exploitable weaknesses from history.
Location: integrations/lol-history/src/lol_history/opponent_weakness_detector.py
Reference: Seraphine opponent profiling, leagueoflegends-optimizer feature engineering
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.opponent_weakness_detector.v1"

_THRESHOLDS = {"death_rate": 0.25, "cs_per_min": 5.5, "wards_per_min": 0.4}

class OpponentWeaknessDetector:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def detect(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        if not matches:
            self._fire("detect_empty", {})
            return {"weaknesses": []}
        n = len(matches)
        weaknesses: list[dict[str, Any]] = []

        # Compute averages
        total_deaths, total_cs, total_dur, total_wards = 0, 0, 0.0, 0
        total_kills, total_assists = 0, 0
        losses_streak, deaths_increasing = 0, True
        prev_deaths = -1
        has_wards = False

        for m in matches:
            d = m.get("deaths", 0); k = m.get("kills", 0); a = m.get("assists", 0)
            cs = m.get("cs", 0); dur = m.get("duration_minutes", 1) or 1
            total_deaths += d; total_kills += k; total_assists += a
            total_cs += cs; total_dur += dur
            if "wards_placed" in m:
                has_wards = True
                total_wards += m["wards_placed"]
            if not m.get("win"):
                losses_streak += 1
            if prev_deaths >= 0 and d <= prev_deaths:
                deaths_increasing = False
            prev_deaths = d

        avg_death_rate = total_deaths / total_dur if total_dur > 0 else 0
        avg_cs_pm = total_cs / total_dur if total_dur > 0 else 0
        avg_wards_pm = total_wards / total_dur if total_dur > 0 else 0
        avg_kda = (total_kills + total_assists) / max(1, total_deaths)

        # High death rate
        if avg_death_rate > _THRESHOLDS["death_rate"]:
            sev = min(avg_death_rate / 0.5, 1.0)
            weaknesses.append({"type": "high_death_rate", "severity": sev,
                               "recommendation": "Punish aggressive positioning; likely to overextend and die"})

        # Low CS
        if avg_cs_pm < _THRESHOLDS["cs_per_min"]:
            sev = max(0.0, 1.0 - avg_cs_pm / _THRESHOLDS["cs_per_min"])
            weaknesses.append({"type": "low_cs", "severity": sev,
                               "recommendation": "Outfarm opponent; they fall behind in gold passively"})

        # Low vision
        if has_wards and avg_wards_pm < _THRESHOLDS["wards_per_min"]:
            sev = max(0.0, 1.0 - avg_wards_pm / _THRESHOLDS["wards_per_min"])
            weaknesses.append({"type": "low_vision", "severity": sev,
                               "recommendation": "Exploit poor vision; roam and set up ganks in blind spots"})

        # Tilt pattern: consecutive losses with increasing deaths
        if losses_streak >= 3 and deaths_increasing and n >= 3:
            weaknesses.append({"type": "tilt_pattern", "severity": min(losses_streak / 5.0, 1.0),
                               "recommendation": "Opponent may be tilted; apply early pressure to tilt further"})

        # Strong player: no weaknesses if KDA > 5 and CS > 7/min
        if avg_kda > 5.0 and avg_cs_pm > 7.0:
            weaknesses = []

        # Sort by severity desc
        weaknesses.sort(key=lambda w: w["severity"], reverse=True)
        self._fire("detect_complete", {"weakness_count": len(weaknesses)})
        return {"weaknesses": weaknesses}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
