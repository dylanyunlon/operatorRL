"""
Playstyle Classifier — Classify player playstyle (aggressive/passive/farm/supportive).
Location: integrations/lol/src/lol_agent/playstyle_classifier.py
Reference: leagueoflegends-optimizer player features, DI-star agent categorization
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.playstyle_classifier.v1"

class PlaystyleClassifier:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def classify(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        if not matches:
            self._fire("classify_empty", {})
            return {"playstyle": "unknown", "scores": {"aggressive": 0, "passive": 0, "farm": 0, "supportive": 0}}

        n = len(matches)
        total_k, total_d, total_a, total_cs, total_dur = 0, 0, 0, 0, 0.0
        total_fb, total_wards = 0, 0

        for m in matches:
            total_k += m.get("kills", 0)
            total_d += m.get("deaths", 0)
            total_a += m.get("assists", 0)
            total_cs += m.get("cs", 0)
            total_dur += m.get("duration_minutes", 1) or 1
            if m.get("first_blood"):
                total_fb += 1
            total_wards += m.get("wards_placed", 0)

        kpm = total_k / total_dur if total_dur > 0 else 0
        apm = total_a / total_dur if total_dur > 0 else 0
        cspm = total_cs / total_dur if total_dur > 0 else 0
        wpm = total_wards / total_dur if total_dur > 0 else 0
        fb_rate = total_fb / n

        # Scoring — weights tuned to differentiate playstyles
        # Aggressive: high kills/min + first blood
        aggressive = min(1.0, (kpm / 0.4) * 0.5 + fb_rate * 0.3 + min(total_d / total_dur / 0.3, 1.0) * 0.2)
        # Farm: high CS but NOT high assists/wards (pure farmer)
        farm_raw = min(1.0, cspm / 10.0)
        assist_suppress = min(1.0, apm / 0.3)  # High assists suppress farm label
        ward_suppress = min(1.0, wpm / 0.8)
        farm = farm_raw * max(0.2, 1.0 - (assist_suppress * 0.4 + ward_suppress * 0.3))
        # Supportive: high assists + high wards
        supportive = min(1.0, (apm / 0.5) * 0.4 + (wpm / 1.5) * 0.6)
        # Passive: low kills, moderate everything else
        passive = min(1.0, max(0, 1.0 - aggressive) * 0.5 + (1.0 - min(kpm / 0.3, 1.0)) * 0.3 + min(wpm / 1.0, 1.0) * 0.2)

        scores = {"aggressive": aggressive, "passive": passive, "farm": farm, "supportive": supportive}

        # Determine dominant style
        best = max(scores, key=scores.get)
        # If scores are close, label as balanced
        vals = list(scores.values())
        if max(vals) - min(vals) < 0.15:
            best = "balanced"

        self._fire("classify_complete", {"playstyle": best, "n": n})
        return {"playstyle": best, "scores": scores}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
