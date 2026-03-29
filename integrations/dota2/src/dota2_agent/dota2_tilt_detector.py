"""
Dota2 Tilt Detector — Detect tilt from losing streaks and behavior patterns.

Location: integrations/dota2/src/dota2_agent/dota2_tilt_detector.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.dota2.dota2_tilt_detector.v1"

class Dota2TiltDetector:
    """Detect player tilt from recent match history."""

    def __init__(self, window: int = 5) -> None:
        self.window = window
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def detect(self, recent_matches: list[dict[str, Any]]) -> dict[str, Any]:
        window = recent_matches[:self.window]
        if not window:
            return {"tilted": False, "confidence": 0.0, "loss_streak": 0}
        losses = 0
        for m in window:
            if not m.get("win", True):
                losses += 1
            else:
                break
        avg_deaths = sum(m.get("deaths", 0) for m in window) / len(window)
        abandons = sum(1 for m in window if m.get("abandon", False))
        tilted = losses >= 3 or (losses >= 2 and avg_deaths > 8) or abandons > 0
        confidence = min((losses / self.window + min(avg_deaths / 15.0, 1.0)) / 2, 1.0)
        self._fire_evolution("tilt_detected", {"tilted": tilted, "losses": losses})
        return {"tilted": tilted, "confidence": confidence, "loss_streak": losses, "avg_deaths": avg_deaths, "abandons": abandons}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
