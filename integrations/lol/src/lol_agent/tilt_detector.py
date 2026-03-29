"""
Tilt Detector — Detect tilt from recent match loss patterns.
Location: integrations/lol/src/lol_agent/tilt_detector.py
Reference: Seraphine match history analysis, leagueoflegends-optimizer
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.tilt_detector.v1"

class TiltDetector:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def detect(self, matches: list[dict[str, Any]]) -> dict[str, Any]:
        if not matches:
            self._fire("detect_empty", {})
            return {"tilt_score": 0.0, "is_tilted": False, "indicators": []}

        n = len(matches)
        indicators: list[str] = []
        score = 0.0

        # Loss streak analysis (most recent first — matches in chronological order)
        losses = sum(1 for m in matches if not m.get("win"))
        loss_ratio = losses / n
        score += loss_ratio * 0.3

        # Consecutive losses at end
        consecutive_losses = 0
        for m in reversed(matches):
            if not m.get("win"):
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= 2:
            score += min(consecutive_losses / 5.0, 0.3)
            indicators.append(f"Losing streak: {consecutive_losses} consecutive losses")

        # Increasing deaths pattern
        deaths = [m.get("deaths", 0) for m in matches]
        if len(deaths) >= 2:
            increasing = all(deaths[i] <= deaths[i + 1] for i in range(len(deaths) - 1))
            if increasing and losses >= 2:
                score += 0.2
                indicators.append("Deaths increasing across games")

        # Short game surrenders (< 18 min)
        short_losses = sum(1 for m in matches if not m.get("win") and m.get("duration_minutes", 30) < 18)
        if short_losses >= 1:
            score += short_losses / n * 0.2
            indicators.append(f"Short game surrenders: {short_losses}")

        # Recovery: recent wins reduce tilt
        recent_wins = sum(1 for m in matches[-2:] if m.get("win")) if len(matches) >= 2 else 0
        if recent_wins > 0:
            score *= (1.0 - recent_wins * 0.25)

        score = min(max(score, 0.0), 1.0)
        is_tilted = score > 0.5

        self._fire("detect_complete", {"tilt_score": score, "is_tilted": is_tilted})
        return {"tilt_score": score, "is_tilted": is_tilted, "indicators": indicators}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})
