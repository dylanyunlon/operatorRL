"""
RealtimeMinimapTracker — Tracks player positions for MIA detection and gank prediction.

Architecture (拿来主义):
  minimap_annotator.py — minimap annotation/overlay patterns
  danger_zone_detector.py — danger zone calculation, proximity alerts

Location: integrations/lol-history/src/lol_history/realtime_minimap_tracker.py

Design Notes (Knuth-level critique):
  User:
    - MIA alerts when enemy position data goes stale (>15s no update).
    - Clustering detection identifies team fights and split pushers.
    - Historical heatmap correlation predicts likely gank routes.
  System:
    - Position data stored per-player in bounded deques.
    - Euclidean distance on SR coordinates (14870x14870 map).
    - MIA detection is polling-based: check_mia with current timestamp.
    - Trajectory smoothing via exponential moving average.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.realtime_minimap_tracker.v1"

SR_MAP_WIDTH = 14870.0
SR_MAP_HEIGHT = 14870.0
MIA_THRESHOLD_SECONDS = 15.0
GANK_PROXIMITY_THRESHOLD = 2000.0
CLUSTERING_RADIUS = 1500.0


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


def _euclidean_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def _normalize_position(x: float, y: float) -> Tuple[float, float]:
    """Normalize position to [0, 1] range on SR map."""
    return (x / SR_MAP_WIDTH, y / SR_MAP_HEIGHT)


class _PositionRecord:
    """Single position record with metadata."""
    __slots__ = ("x", "y", "game_time", "wall_time", "speed", "heading")

    def __init__(self, x: float, y: float, game_time: float,
                 speed: float = 0.0, heading: float = 0.0) -> None:
        self.x = x
        self.y = y
        self.game_time = game_time
        self.wall_time = time.monotonic()
        self.speed = speed
        self.heading = heading

    def to_dict(self) -> Dict[str, Any]:
        return {"x": self.x, "y": self.y, "game_time": self.game_time,
                "speed": self.speed, "heading": self.heading}


class _PlayerTrajectory:
    """Stores and analyzes a player's movement trajectory."""

    def __init__(self, player_name: str, max_points: int = 300) -> None:
        self.player_name = player_name
        self._positions: deque = deque(maxlen=max_points)
        self._last_position: Optional[_PositionRecord] = None
        self._total_distance = 0.0
        self._update_count = 0
        self._ema_x = 0.0
        self._ema_y = 0.0
        self._ema_alpha = 0.3

    def add_position(self, x: float, y: float, game_time: float) -> Dict[str, Any]:
        self._update_count += 1
        speed = 0.0
        heading = 0.0

        if self._last_position:
            dist = _euclidean_distance(x, y, self._last_position.x, self._last_position.y)
            dt = game_time - self._last_position.game_time
            if dt > 0:
                speed = dist / dt
            if dist > 0:
                heading = math.atan2(y - self._last_position.y,
                                     x - self._last_position.x)
            self._total_distance += dist
        else:
            self._ema_x = x
            self._ema_y = y

        self._ema_x = self._ema_alpha * x + (1 - self._ema_alpha) * self._ema_x
        self._ema_y = self._ema_alpha * y + (1 - self._ema_alpha) * self._ema_y

        record = _PositionRecord(x, y, game_time, speed, heading)
        self._positions.append(record)
        self._last_position = record

        return {
            "speed": speed,
            "heading": heading,
            "total_distance": self._total_distance,
            "smoothed_x": self._ema_x,
            "smoothed_y": self._ema_y,
        }

    def get_latest(self) -> Optional[Dict[str, Any]]:
        if not self._last_position:
            return None
        return {
            **self._last_position.to_dict(),
            "smoothed_x": self._ema_x,
            "smoothed_y": self._ema_y,
        }

    def get_trajectory(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in list(self._positions)[-limit:]]

    def get_time_since_update(self, current_game_time: float) -> float:
        if not self._last_position:
            return float("inf")
        return current_game_time - self._last_position.game_time

    def get_average_speed(self, window: int = 10) -> float:
        recent = list(self._positions)[-window:]
        if len(recent) < 2:
            return 0.0
        speeds = [p.speed for p in recent if p.speed > 0]
        return _safe_div(sum(speeds), len(speeds)) if speeds else 0.0

    def predict_position(self, seconds_ahead: float) -> Dict[str, float]:
        """Predict future position based on current velocity."""
        if not self._last_position or self._last_position.speed == 0:
            return {"x": self._ema_x, "y": self._ema_y, "confidence": 0.0}
        pred_x = self._last_position.x + math.cos(self._last_position.heading) * self._last_position.speed * seconds_ahead
        pred_y = self._last_position.y + math.sin(self._last_position.heading) * self._last_position.speed * seconds_ahead
        pred_x = max(0, min(SR_MAP_WIDTH, pred_x))
        pred_y = max(0, min(SR_MAP_HEIGHT, pred_y))
        confidence = max(0.0, 1.0 - seconds_ahead / 10.0)
        return {"x": pred_x, "y": pred_y, "confidence": confidence}

    def get_stats(self) -> Dict[str, Any]:
        return {
            "player": self.player_name,
            "updates": self._update_count,
            "positions_stored": len(self._positions),
            "total_distance": self._total_distance,
            "avg_speed": self.get_average_speed(),
        }


class _MIADetector:
    """Detects MIA (Missing In Action) players based on position staleness."""

    def __init__(self, threshold_seconds: float = MIA_THRESHOLD_SECONDS) -> None:
        self._threshold = threshold_seconds
        self._mia_alerts: deque = deque(maxlen=200)
        self._alert_count = 0
        self._active_mia: Dict[str, float] = {}

    def check_players(self, trajectories: Dict[str, _PlayerTrajectory],
                      known_players: List[str],
                      current_game_time: float) -> List[Dict[str, Any]]:
        mia_list = []
        for player in known_players:
            traj = trajectories.get(player)
            if not traj:
                mia_list.append({"player": player, "reason": "no_data",
                                 "stale_seconds": float("inf")})
                continue
            stale = traj.get_time_since_update(current_game_time)
            if stale > self._threshold:
                was_mia = player in self._active_mia
                if not was_mia:
                    self._alert_count += 1
                    self._active_mia[player] = current_game_time
                    alert = {"player": player, "stale_seconds": stale,
                             "game_time": current_game_time,
                             "alert_num": self._alert_count}
                    self._mia_alerts.append(alert)
                mia_list.append({"player": player, "reason": "stale_position",
                                 "stale_seconds": stale})
            else:
                if player in self._active_mia:
                    del self._active_mia[player]
        return mia_list

    def get_active_mia(self) -> Dict[str, float]:
        return dict(self._active_mia)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "threshold": self._threshold,
            "alert_count": self._alert_count,
            "active_mia": len(self._active_mia),
            "recent_alerts": list(self._mia_alerts)[-10:],
        }


class _ClusteringEngine:
    """Detects player clusters (team fights, ganks, split pushers)."""

    def __init__(self, cluster_radius: float = CLUSTERING_RADIUS) -> None:
        self._radius = cluster_radius
        self._cluster_history: deque = deque(maxlen=100)

    def compute_clusters(self, positions: Dict[str, Tuple[float, float]]) -> List[Dict[str, Any]]:
        """Simple single-linkage clustering of player positions."""
        if not positions:
            return []
        players = list(positions.keys())
        assigned = set()
        clusters = []

        for i, p1 in enumerate(players):
            if p1 in assigned:
                continue
            cluster = [p1]
            assigned.add(p1)
            for j, p2 in enumerate(players):
                if p2 in assigned or j <= i:
                    continue
                dist = _euclidean_distance(
                    positions[p1][0], positions[p1][1],
                    positions[p2][0], positions[p2][1])
                if dist <= self._radius:
                    cluster.append(p2)
                    assigned.add(p2)
            if len(cluster) >= 2:
                xs = [positions[p][0] for p in cluster]
                ys = [positions[p][1] for p in cluster]
                centroid_x = sum(xs) / len(xs)
                centroid_y = sum(ys) / len(ys)
                clusters.append({
                    "players": cluster,
                    "size": len(cluster),
                    "centroid": {"x": centroid_x, "y": centroid_y},
                    "is_teamfight": len(cluster) >= 4,
                })
        return clusters

    def analyze(self, trajectories: Dict[str, _PlayerTrajectory],
                current_game_time: float) -> Dict[str, Any]:
        positions = {}
        for name, traj in trajectories.items():
            latest = traj.get_latest()
            if latest and traj.get_time_since_update(current_game_time) < 10.0:
                positions[name] = (latest["x"], latest["y"])

        clusters = self.compute_clusters(positions)
        isolated = [p for p in positions if not any(p in c["players"] for c in clusters)]

        result = {
            "clusters": clusters,
            "isolated_players": isolated,
            "total_positioned": len(positions),
            "teamfight_detected": any(c["is_teamfight"] for c in clusters),
        }
        self._cluster_history.append({"ts": current_game_time, **result})
        return result


class _HeatmapCorrelator:
    """Correlates current positions with historical heatmaps for gank prediction."""

    def __init__(self, grid_size: int = 20) -> None:
        self._grid_size = grid_size
        self._cell_width = SR_MAP_WIDTH / grid_size
        self._cell_height = SR_MAP_HEIGHT / grid_size
        self._historical_heatmap: Dict[str, List[List[int]]] = {}
        self._visit_counts: Dict[str, List[List[int]]] = {}

    def _get_cell(self, x: float, y: float) -> Tuple[int, int]:
        col = min(int(x / self._cell_width), self._grid_size - 1)
        row = min(int(y / self._cell_height), self._grid_size - 1)
        return max(0, row), max(0, col)

    def record_position(self, player: str, x: float, y: float) -> None:
        if player not in self._visit_counts:
            self._visit_counts[player] = [[0] * self._grid_size
                                          for _ in range(self._grid_size)]
        r, c = self._get_cell(x, y)
        self._visit_counts[player][r][c] += 1

    def load_historical_heatmap(self, player: str,
                                 heatmap: List[List[int]]) -> None:
        self._historical_heatmap[player] = heatmap

    def get_gank_probability(self, player: str, target_x: float,
                              target_y: float) -> float:
        """Estimate gank probability based on historical movement patterns."""
        hm = self._historical_heatmap.get(player) or self._visit_counts.get(player)
        if not hm:
            return 0.0
        r, c = self._get_cell(target_x, target_y)
        total_visits = sum(sum(row) for row in hm)
        if total_visits == 0:
            return 0.0
        cell_visits = hm[r][c]
        neighbors = 0
        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < self._grid_size and 0 <= nc < self._grid_size:
                    neighbors += hm[nr][nc]
        return min(1.0, _safe_div(neighbors, total_visits) * 10.0)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "grid_size": self._grid_size,
            "historical_players": len(self._historical_heatmap),
            "current_players": len(self._visit_counts),
        }


class RealtimeMinimapTracker:
    """Tracks player positions on minimap with MIA detection and gank prediction.

    Public API: update_position, get_trajectory, detect_mia, get_clustering,
                predict_gank, get_player_speed, get_all_positions, get_stats
    """

    def __init__(self, mia_threshold: float = MIA_THRESHOLD_SECONDS,
                 cluster_radius: float = CLUSTERING_RADIUS) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._update_count = 0
        self._trajectories: Dict[str, _PlayerTrajectory] = {}
        self._mia_detector = _MIADetector(threshold_seconds=mia_threshold)
        self._clustering = _ClusteringEngine(cluster_radius=cluster_radius)
        self._heatmap = _HeatmapCorrelator()
        self._latest_game_time = 0.0

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _ensure_trajectory(self, player: str) -> _PlayerTrajectory:
        if player not in self._trajectories:
            self._trajectories[player] = _PlayerTrajectory(player)
        return self._trajectories[player]

    def update_position(self, player: str, x: float, y: float,
                         game_time: float) -> Dict[str, Any]:
        self._op_count += 1
        self._update_count += 1
        if game_time > self._latest_game_time:
            self._latest_game_time = game_time

        traj = self._ensure_trajectory(player)
        motion = traj.add_position(x, y, game_time)
        self._heatmap.record_position(player, x, y)

        return {
            "status": "ok",
            "player": player,
            "position": {"x": x, "y": y},
            "normalized": {"x": x / SR_MAP_WIDTH, "y": y / SR_MAP_HEIGHT},
            "motion": motion,
        }

    def get_trajectory(self, player: str,
                        limit: int = 50) -> Dict[str, Any]:
        self._op_count += 1
        traj = self._trajectories.get(player)
        if not traj:
            return {"status": "ok", "player": player, "found": False}
        return {
            "status": "ok",
            "player": player,
            "found": True,
            "trajectory": traj.get_trajectory(limit),
            "stats": traj.get_stats(),
        }

    def detect_mia(self, known_enemy_players: List[str],
                    current_game_time: float = None) -> Dict[str, Any]:
        self._op_count += 1
        gt = current_game_time or self._latest_game_time
        mia_list = self._mia_detector.check_players(
            self._trajectories, known_enemy_players, gt)

        if mia_list:
            self._fire("mia_detected", {
                "mia_count": len(mia_list),
                "players": [m["player"] for m in mia_list],
            })

        return {
            "status": "ok",
            "game_time": gt,
            "mia_players": mia_list,
            "mia_count": len(mia_list),
        }

    def get_clustering(self, current_game_time: float = None) -> Dict[str, Any]:
        self._op_count += 1
        gt = current_game_time or self._latest_game_time
        analysis = self._clustering.analyze(self._trajectories, gt)
        return {"status": "ok", "game_time": gt, **analysis}

    def predict_gank(self, ganker: str, target_x: float,
                      target_y: float) -> Dict[str, Any]:
        self._op_count += 1
        prob = self._heatmap.get_gank_probability(ganker, target_x, target_y)
        traj = self._trajectories.get(ganker)
        prediction = None
        if traj:
            prediction = traj.predict_position(5.0)
        return {
            "status": "ok",
            "ganker": ganker,
            "target_position": {"x": target_x, "y": target_y},
            "gank_probability": prob,
            "predicted_position_5s": prediction,
        }

    def get_player_speed(self, player: str) -> Dict[str, Any]:
        self._op_count += 1
        traj = self._trajectories.get(player)
        if not traj:
            return {"status": "ok", "player": player, "found": False}
        return {
            "status": "ok",
            "player": player,
            "average_speed": traj.get_average_speed(),
            "latest": traj.get_latest(),
        }

    def get_all_positions(self) -> Dict[str, Any]:
        self._op_count += 1
        positions = {}
        for name, traj in self._trajectories.items():
            latest = traj.get_latest()
            if latest:
                positions[name] = latest
        return {
            "status": "ok",
            "positions": positions,
            "total_tracked": len(positions),
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "update_count": self._update_count,
            "tracked_players": len(self._trajectories),
            "latest_game_time": self._latest_game_time,
            "mia_detector": self._mia_detector.get_stats(),
            "heatmap": self._heatmap.get_stats(),
            "per_player": {n: t.get_stats() for n, t in self._trajectories.items()},
        }
