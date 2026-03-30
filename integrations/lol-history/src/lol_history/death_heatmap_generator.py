"""
DeathHeatmapGenerator — Grid-based death heatmap from spatial position data.

Architecture (拿来主义):
  查看 **lane_state_tracker.py** 的空间位置数据处理方式。
  实现 **DeathHeatmapGenerator**，支持网格分桶、热点检测和危险区域标注。

Location: integrations/lol-history/src/lol_history/death_heatmap_generator.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.death_heatmap_generator.v1"

EARLY_PHASE_END = 900
MID_PHASE_END = 1800


class DeathHeatmapGenerator:
    """Generate grid-based death heatmaps."""

    def __init__(self, grid_size: int = 500) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._grid_size = grid_size
        self._deaths: List[Dict[str, Any]] = []

    def _bucket(self, x: float, y: float) -> Tuple[int, int]:
        return (int(x) // self._grid_size, int(y) // self._grid_size)

    def record_death(self, x: float, y: float, timestamp: int, champion: str) -> None:
        self._deaths.append({"x": x, "y": y, "timestamp": timestamp, "champion": champion,
                             "bucket": self._bucket(x, y)})

    def get_death_count(self) -> int:
        return len(self._deaths)

    def generate_heatmap(
        self, phase: Optional[str] = None, champion: Optional[str] = None,
    ) -> Dict[Tuple[int, int], int]:
        filtered = self._deaths
        if phase == "early":
            filtered = [d for d in filtered if d["timestamp"] <= EARLY_PHASE_END]
        elif phase == "mid":
            filtered = [d for d in filtered if EARLY_PHASE_END < d["timestamp"] <= MID_PHASE_END]
        elif phase == "late":
            filtered = [d for d in filtered if d["timestamp"] > MID_PHASE_END]
        if champion:
            filtered = [d for d in filtered if d["champion"] == champion]
        heatmap: Dict[Tuple[int, int], int] = defaultdict(int)
        for d in filtered:
            heatmap[d["bucket"]] += 1
        return dict(heatmap)

    def get_hotspots(self, n: int = 5) -> List[Dict[str, Any]]:
        heatmap = self.generate_heatmap()
        if not heatmap:
            return []
        sorted_cells = sorted(heatmap.items(), key=lambda x: x[1], reverse=True)
        return [{"bucket": k, "count": v} for k, v in sorted_cells[:n]]

    def get_danger_zones(self, threshold: int = 3) -> List[Dict[str, Any]]:
        heatmap = self.generate_heatmap()
        return [{"bucket": k, "count": v} for k, v in heatmap.items() if v >= threshold]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_deaths": len(self._deaths),
            "grid_size": self._grid_size,
            "hotspots": self.get_hotspots(n=10),
        }
