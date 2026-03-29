"""
Live Danger Fuser — Fuse real-time danger_zone with historical death heatmaps.

Location: integrations/lol/src/lol_agent/live_danger_fuser.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/danger_zone_detector.py: real-time danger
  - LeagueAI/LeagueAI_helper.py: position-based detection
"""

from __future__ import annotations
import logging, math, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.live_danger_fuser.v1"

class LiveDangerFuser:
    """Fuse real-time danger with historical death data."""

    def __init__(self, history_weight: float = 0.3) -> None:
        self.history_weight = history_weight
        self._death_heatmap: dict[tuple[int, int], float] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_death_location(self, x: float, y: float, weight: float = 1.0) -> None:
        grid = (int(x // 500), int(y // 500))
        self._death_heatmap[grid] = self._death_heatmap.get(grid, 0.0) + weight

    def fuse(self, position: tuple[float, float], live_danger: float, enemies: list[dict[str, Any]] = None) -> dict[str, Any]:
        enemies = enemies or []
        grid = (int(position[0] // 500), int(position[1] // 500))
        hist_danger = self._death_heatmap.get(grid, 0.0)
        max_hist = max(self._death_heatmap.values()) if self._death_heatmap else 1.0
        hist_norm = min(hist_danger / max(max_hist, 1.0), 1.0)
        fused = (1 - self.history_weight) * live_danger + self.history_weight * hist_norm
        fused = max(0.0, min(1.0, fused))
        self._fire_evolution("danger_fused", {"fused": fused, "live": live_danger, "hist": hist_norm})
        return {"fused_danger": fused, "live_component": live_danger, "history_component": hist_norm, "is_safe": fused < 0.5}

    def clear_heatmap(self) -> None:
        self._death_heatmap.clear()

    def heatmap_size(self) -> int:
        return len(self._death_heatmap)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
