"""
RealtimeGoldXpTracker — Tracks gold and XP for all players with trend/spike detection.

Architecture (拿来主义):
  gold_efficiency_tracker.py — gold efficiency calculation patterns
  live_game_state_enricher.py — state enrichment with derived metrics

Location: integrations/lol-history/src/lol_history/realtime_gold_xp_tracker.py

Design Notes (Knuth-level critique):
  User:
    - Provides real-time gold/XP diffs between teams and individuals.
    - Detects gold spikes (bounty kills, shutdowns, plate gold) for decision urgency.
    - Trend lines help predict power spikes and gold crossover points.
  System:
    - Per-player time-series stored in bounded deques (memory-safe).
    - Spike detection uses configurable threshold + sliding window delta.
    - Team aggregation is computed on-demand, not stored (avoids stale team data).
    - Efficiency metrics (gold/min, XP/min) computed from time-series deltas.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.realtime_gold_xp_tracker.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


# ─── Time Series Store ────────────────────────────────────────────────────────

class _PlayerTimeSeries:
    """Bounded time-series for a single player's gold and XP data."""

    def __init__(self, player_name: str, max_points: int = 500) -> None:
        self.player_name = player_name
        self._gold_series: deque = deque(maxlen=max_points)
        self._xp_series: deque = deque(maxlen=max_points)
        self._update_count = 0
        self._last_gold = 0.0
        self._last_xp = 0.0
        self._last_timestamp = 0.0

    def add_point(self, gold: float, xp: float, game_time: float) -> Dict[str, Any]:
        self._update_count += 1
        gold_delta = gold - self._last_gold if self._last_gold > 0 else 0.0
        xp_delta = xp - self._last_xp if self._last_xp > 0 else 0.0
        time_delta = game_time - self._last_timestamp if self._last_timestamp > 0 else 0.0

        point = {
            "gold": gold, "xp": xp, "game_time": game_time,
            "gold_delta": gold_delta, "xp_delta": xp_delta,
            "time_delta": time_delta,
            "gold_per_min": _safe_div(gold, game_time / 60.0) if game_time > 0 else 0.0,
            "xp_per_min": _safe_div(xp, game_time / 60.0) if game_time > 0 else 0.0,
        }
        self._gold_series.append({"ts": game_time, "value": gold, "delta": gold_delta})
        self._xp_series.append({"ts": game_time, "value": xp, "delta": xp_delta})
        self._last_gold = gold
        self._last_xp = xp
        self._last_timestamp = game_time
        return point

    def get_latest(self) -> Dict[str, Any]:
        return {
            "gold": self._last_gold, "xp": self._last_xp,
            "timestamp": self._last_timestamp,
            "updates": self._update_count,
        }

    def get_trend(self, window: int = 10) -> Dict[str, Any]:
        """Compute trend over last N data points."""
        gold_points = list(self._gold_series)[-window:]
        xp_points = list(self._xp_series)[-window:]
        if len(gold_points) < 2:
            return {"gold_trend": 0.0, "xp_trend": 0.0, "data_points": len(gold_points)}

        gold_deltas = [p["delta"] for p in gold_points if p["delta"] != 0]
        xp_deltas = [p["delta"] for p in xp_points if p["delta"] != 0]

        avg_gold_delta = _safe_div(sum(gold_deltas), len(gold_deltas)) if gold_deltas else 0.0
        avg_xp_delta = _safe_div(sum(xp_deltas), len(xp_deltas)) if xp_deltas else 0.0

        gold_slope = self._compute_slope([p["value"] for p in gold_points])
        xp_slope = self._compute_slope([p["value"] for p in xp_points])

        return {
            "gold_trend": gold_slope,
            "xp_trend": xp_slope,
            "avg_gold_delta": avg_gold_delta,
            "avg_xp_delta": avg_xp_delta,
            "data_points": len(gold_points),
            "gold_acceleration": self._compute_acceleration(gold_deltas),
            "xp_acceleration": self._compute_acceleration(xp_deltas),
        }

    def _compute_slope(self, values: List[float]) -> float:
        """Simple linear regression slope."""
        n = len(values)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        return _safe_div(numerator, denominator)

    def _compute_acceleration(self, deltas: List[float]) -> float:
        """Compute acceleration (change in rate of change)."""
        if len(deltas) < 3:
            return 0.0
        recent_half = deltas[len(deltas) // 2:]
        earlier_half = deltas[:len(deltas) // 2]
        recent_avg = _safe_div(sum(recent_half), len(recent_half))
        earlier_avg = _safe_div(sum(earlier_half), len(earlier_half))
        return recent_avg - earlier_avg

    def get_gold_series(self, limit: int = 50) -> List[Dict]:
        return list(self._gold_series)[-limit:]

    def get_xp_series(self, limit: int = 50) -> List[Dict]:
        return list(self._xp_series)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "player": self.player_name,
            "updates": self._update_count,
            "gold_points": len(self._gold_series),
            "xp_points": len(self._xp_series),
            "latest_gold": self._last_gold,
            "latest_xp": self._last_xp,
        }


# ─── Spike Detector ──────────────────────────────────────────────────────────

class _GoldSpikeDetector:
    """Detects sudden gold spikes (bounties, shutdowns, plate gold)."""

    def __init__(self, spike_threshold: float = 500.0,
                 window_size: int = 5) -> None:
        self._threshold = spike_threshold
        self._window_size = window_size
        self._detected_spikes: deque = deque(maxlen=200)
        self._spike_count = 0

    def check_spike(self, player: str, gold_delta: float,
                     game_time: float) -> Optional[Dict[str, Any]]:
        if abs(gold_delta) >= self._threshold:
            self._spike_count += 1
            spike_type = self._classify_spike(gold_delta)
            spike = {
                "player": player,
                "gold_delta": gold_delta,
                "game_time": game_time,
                "spike_type": spike_type,
                "spike_num": self._spike_count,
            }
            self._detected_spikes.append(spike)
            return spike
        return None

    def _classify_spike(self, delta: float) -> str:
        """Heuristic classification of gold spike source."""
        abs_delta = abs(delta)
        if abs_delta >= 1000:
            return "shutdown_bounty"
        elif abs_delta >= 700:
            return "kill_bounty_high"
        elif abs_delta >= 500:
            return "kill_or_plate"
        return "unknown"

    def get_recent_spikes(self, limit: int = 20) -> List[Dict]:
        return list(self._detected_spikes)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "spike_count": self._spike_count,
            "threshold": self._threshold,
            "recent_spikes": len(self._detected_spikes),
        }


# ─── Team Aggregator ─────────────────────────────────────────────────────────

class _TeamAggregator:
    """Computes team-level gold/XP metrics from individual player data."""

    @staticmethod
    def compute_team_totals(player_series: Dict[str, _PlayerTimeSeries],
                            team_players: List[str]) -> Dict[str, Any]:
        total_gold = 0.0
        total_xp = 0.0
        player_breakdown = []
        for p in team_players:
            ts = player_series.get(p)
            if ts:
                latest = ts.get_latest()
                total_gold += latest["gold"]
                total_xp += latest["xp"]
                player_breakdown.append({
                    "player": p, "gold": latest["gold"], "xp": latest["xp"],
                    "gold_share": 0.0,
                })
        for pb in player_breakdown:
            pb["gold_share"] = _safe_div(pb["gold"], total_gold) if total_gold > 0 else 0.0
        return {
            "total_gold": total_gold,
            "total_xp": total_xp,
            "player_count": len(player_breakdown),
            "players": player_breakdown,
        }

    @staticmethod
    def compute_diff(team_a: Dict[str, Any],
                     team_b: Dict[str, Any]) -> Dict[str, Any]:
        gold_diff = team_a.get("total_gold", 0) - team_b.get("total_gold", 0)
        xp_diff = team_a.get("total_xp", 0) - team_b.get("total_xp", 0)
        return {
            "gold_diff": gold_diff,
            "xp_diff": xp_diff,
            "gold_advantage": "team_a" if gold_diff > 0 else ("team_b" if gold_diff < 0 else "even"),
            "xp_advantage": "team_a" if xp_diff > 0 else ("team_b" if xp_diff < 0 else "even"),
            "gold_diff_pct": _safe_div(abs(gold_diff),
                                       max(team_a.get("total_gold", 1),
                                           team_b.get("total_gold", 1))) * 100,
        }


# ─── Efficiency Calculator ───────────────────────────────────────────────────

class _EfficiencyCalculator:
    """Calculates gold and XP efficiency metrics."""

    @staticmethod
    def compute_efficiency(gold: float, xp: float, game_time: float,
                           cs: int = 0, kills: int = 0,
                           assists: int = 0) -> Dict[str, Any]:
        minutes = game_time / 60.0 if game_time > 0 else 1.0
        return {
            "gold_per_min": _safe_div(gold, minutes),
            "xp_per_min": _safe_div(xp, minutes),
            "cs_per_min": _safe_div(cs, minutes) if cs else None,
            "gold_per_cs": _safe_div(gold, cs) if cs else None,
            "kda_gold_share": _safe_div(kills + assists, max(1, kills + assists)) * gold if kills or assists else 0,
            "efficiency_score": _safe_div(gold + xp * 0.5, minutes),
        }


# ─── Power Spike Predictor ──────────────────────────────────────────────────

class _PowerSpikePredictor:
    """Predicts upcoming power spikes based on gold trajectory and item thresholds."""

    COMMON_ITEM_COSTS = {
        "Infinity Edge": 3400,
        "Trinity Force": 3333,
        "Kraken Slayer": 3100,
        "Luden's Companion": 2900,
        "Riftmaker": 3100,
        "Jak'Sho": 3200,
        "Sundered Sky": 3100,
    }

    def predict_next_spike(self, current_gold: float,
                            gold_per_min: float,
                            items_owned: int = 0) -> Dict[str, Any]:
        next_item_cost = 3200 - (current_gold % 3200)
        if gold_per_min > 0:
            minutes_to_spike = next_item_cost / gold_per_min
        else:
            minutes_to_spike = float("inf")

        return {
            "estimated_gold_needed": next_item_cost,
            "estimated_minutes": round(minutes_to_spike, 1) if minutes_to_spike != float("inf") else None,
            "items_owned": items_owned,
            "next_item_tier": items_owned + 1,
            "is_power_spike_imminent": minutes_to_spike < 2.0,
        }


class RealtimeGoldXpTracker:
    """Tracks gold and XP for all players with trend analysis and spike detection.

    Public API: update_player, get_player_trend, get_team_gold_diff,
                detect_gold_spike, get_efficiency, get_power_spike_prediction,
                get_all_players_summary, get_gold_timeline, get_stats
    """

    def __init__(self, spike_threshold: float = 500.0,
                 max_history_per_player: int = 500) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._update_count = 0
        self._player_series: Dict[str, _PlayerTimeSeries] = {}
        self._spike_detector = _GoldSpikeDetector(spike_threshold=spike_threshold)
        self._team_aggregator = _TeamAggregator()
        self._efficiency_calc = _EfficiencyCalculator()
        self._spike_predictor = _PowerSpikePredictor()
        self._max_history = max_history_per_player
        self._gold_diff_history: deque = deque(maxlen=500)
        self._last_update_time = 0.0

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _ensure_player(self, player_name: str) -> _PlayerTimeSeries:
        if player_name not in self._player_series:
            self._player_series[player_name] = _PlayerTimeSeries(
                player_name, max_points=self._max_history)
        return self._player_series[player_name]

    def update_player(self, player_name: str, gold: float, xp: float,
                       game_time: float, cs: int = 0,
                       kills: int = 0, assists: int = 0) -> Dict[str, Any]:
        """Update gold and XP for a player at given game time."""
        self._op_count += 1
        self._update_count += 1
        self._last_update_time = game_time

        ts = self._ensure_player(player_name)
        point = ts.add_point(gold, xp, game_time)

        spike = self._spike_detector.check_spike(
            player_name, point["gold_delta"], game_time)

        efficiency = self._efficiency_calc.compute_efficiency(
            gold, xp, game_time, cs, kills, assists)

        result = {
            "status": "ok",
            "player": player_name,
            "gold": gold, "xp": xp, "game_time": game_time,
            "gold_delta": point["gold_delta"],
            "xp_delta": point["xp_delta"],
            "gold_per_min": point["gold_per_min"],
            "xp_per_min": point["xp_per_min"],
            "efficiency": efficiency,
        }

        if spike:
            result["spike_detected"] = spike
            self._fire("gold_spike", {"player": player_name, "spike": spike})

        return result

    def get_player_trend(self, player_name: str,
                          window: int = 10) -> Dict[str, Any]:
        """Get gold/XP trend for a player."""
        self._op_count += 1
        ts = self._player_series.get(player_name)
        if not ts:
            return {"status": "ok", "player": player_name, "found": False}
        trend = ts.get_trend(window)
        latest = ts.get_latest()
        return {
            "status": "ok",
            "player": player_name,
            "found": True,
            "latest": latest,
            "trend": trend,
        }

    def get_team_gold_diff(self, team_a_players: List[str],
                            team_b_players: List[str]) -> Dict[str, Any]:
        """Compute gold/XP difference between two teams."""
        self._op_count += 1
        team_a = self._team_aggregator.compute_team_totals(
            self._player_series, team_a_players)
        team_b = self._team_aggregator.compute_team_totals(
            self._player_series, team_b_players)
        diff = self._team_aggregator.compute_diff(team_a, team_b)

        self._gold_diff_history.append({
            "ts": self._last_update_time,
            "diff": diff["gold_diff"],
        })

        return {
            "status": "ok",
            "team_a": team_a,
            "team_b": team_b,
            "diff": diff,
        }

    def detect_gold_spike(self, player_name: str) -> Dict[str, Any]:
        """Get recent gold spikes for a player."""
        self._op_count += 1
        spikes = [s for s in self._spike_detector.get_recent_spikes()
                  if s["player"] == player_name]
        return {
            "status": "ok",
            "player": player_name,
            "spikes": spikes,
            "total_spikes": len(spikes),
        }

    def get_efficiency(self, player_name: str,
                        game_time: float = None) -> Dict[str, Any]:
        """Get efficiency metrics for a player."""
        self._op_count += 1
        ts = self._player_series.get(player_name)
        if not ts:
            return {"status": "ok", "player": player_name, "found": False}
        latest = ts.get_latest()
        gt = game_time or latest["timestamp"]
        eff = self._efficiency_calc.compute_efficiency(
            latest["gold"], latest["xp"], gt)
        return {"status": "ok", "player": player_name, "efficiency": eff}

    def get_power_spike_prediction(self, player_name: str,
                                    items_owned: int = 0) -> Dict[str, Any]:
        """Predict next power spike based on gold trajectory."""
        self._op_count += 1
        ts = self._player_series.get(player_name)
        if not ts:
            return {"status": "ok", "player": player_name, "found": False}
        latest = ts.get_latest()
        trend = ts.get_trend(window=10)
        gpm = _safe_div(latest["gold"], latest["timestamp"] / 60.0)
        pred = self._spike_predictor.predict_next_spike(latest["gold"], gpm, items_owned)
        return {
            "status": "ok",
            "player": player_name,
            "prediction": pred,
            "current_gold": latest["gold"],
            "gold_per_min": gpm,
        }

    def get_all_players_summary(self) -> Dict[str, Any]:
        """Get summary of all tracked players."""
        self._op_count += 1
        summaries = {}
        for name, ts in self._player_series.items():
            latest = ts.get_latest()
            trend = ts.get_trend(window=5)
            summaries[name] = {
                "gold": latest["gold"],
                "xp": latest["xp"],
                "gold_trend": trend["gold_trend"],
                "xp_trend": trend["xp_trend"],
                "updates": latest["updates"],
            }
        return {
            "status": "ok",
            "players": summaries,
            "total_players": len(summaries),
        }

    def get_gold_timeline(self, player_name: str,
                           limit: int = 50) -> Dict[str, Any]:
        """Get gold time-series for charting."""
        self._op_count += 1
        ts = self._player_series.get(player_name)
        if not ts:
            return {"status": "ok", "player": player_name, "found": False}
        return {
            "status": "ok",
            "player": player_name,
            "gold_series": ts.get_gold_series(limit),
            "xp_series": ts.get_xp_series(limit),
        }

    def get_gold_diff_timeline(self, limit: int = 50) -> Dict[str, Any]:
        """Get team gold diff over time."""
        self._op_count += 1
        return {
            "status": "ok",
            "timeline": list(self._gold_diff_history)[-limit:],
        }

    def get_stats(self) -> Dict[str, Any]:
        """Full diagnostic stats."""
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "update_count": self._update_count,
            "tracked_players": len(self._player_series),
            "last_update_time": self._last_update_time,
            "spike_detector": self._spike_detector.get_stats(),
            "gold_diff_points": len(self._gold_diff_history),
            "per_player": {n: ts.get_stats()
                          for n, ts in self._player_series.items()},
        }
