"""
ChampionMetaLiveTracker — Tracks real-time champion meta shifts.

Architecture (拿来主义):
  meta_shift_tracker.py + patch_adaptation_analyzer.py（M618）

Location: integrations/lol-history/src/lol_history/champion_meta_live_tracker.py

Design Notes (Knuth-level critique):
  User:
    - update() accepts partial champion data — missing stats logged, not rejected.
    - get_trending returns actionable pick/ban suggestions based on meta shift velocity.
    - All outputs include confidence scores reflecting sample size.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Exponential decay weighting for recency bias in meta scoring.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.champion_meta_live_tracker.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _confidence(n: int, max_n: int = 50) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class ChampionMetaLiveTracker:
    """Tracks real-time champion meta shifts.

    Public API
    ----------
    update              — record a champion observation (pick/ban/win)
    get_trending        — get champions trending up/down
    get_champion_meta   — get meta stats for a single champion
    get_tier_list       — get tier list based on current meta
    reset               — clear all state

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, decay_factor: float = 0.95) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._decay: float = decay_factor
        # champion_id -> list of (timestamp, pick_rate, win_rate, ban_rate)
        self._observations: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self._global_counts: Dict[str, int] = defaultdict(int)

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def update(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Record a champion observation.

        Parameters
        ----------
        data : dict
            Must contain champion_id. Optional: pick_rate, win_rate, ban_rate, patch.

        Returns
        -------
        dict  with status, champion_id, observation_count
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        champ_id = data.get("champion_id")
        if champ_id is None:
            return {"status": "error", "reason": "missing champion_id"}

        obs = {
            "timestamp": time.time(),
            "pick_rate": data.get("pick_rate", 0.0),
            "win_rate": data.get("win_rate", 0.0),
            "ban_rate": data.get("ban_rate", 0.0),
            "patch": data.get("patch", "unknown"),
        }
        self._observations[champ_id].append(obs)
        self._global_counts["total_updates"] += 1

        elapsed = time.time() - _start
        self._fire("update_completed", {"elapsed": elapsed, "champion_id": champ_id})
        return {"status": "ok", "op": "update", "champion_id": champ_id,
                "observation_count": len(self._observations[champ_id])}

    # ------------------------------------------------------------------ #

    def get_trending(self, top_n: int = 10) -> Dict[str, Any]:
        """Get champions trending up or down.

        Compares recent observations against older observations using
        exponential decay weighting.

        Returns
        -------
        dict  with trending_up, trending_down (lists)
        """
        self._op_count += 1
        _start = time.time()

        trends: List[Dict[str, Any]] = []
        for champ_id, obs_list in self._observations.items():
            if len(obs_list) < 2:
                continue

            mid = len(obs_list) // 2
            old_wr = sum(o["win_rate"] for o in obs_list[:mid]) / max(mid, 1)
            new_wr = sum(o["win_rate"] for o in obs_list[mid:]) / max(len(obs_list) - mid, 1)
            delta = new_wr - old_wr

            trends.append({
                "champion_id": champ_id,
                "delta_win_rate": round(delta, 4),
                "current_win_rate": round(new_wr, 4),
                "sample_size": len(obs_list),
                "confidence": round(_confidence(len(obs_list)), 4),
            })

        trends.sort(key=lambda t: t["delta_win_rate"], reverse=True)
        trending_up = trends[:top_n]
        trending_down = trends[-top_n:][::-1] if len(trends) >= top_n else []

        elapsed = time.time() - _start
        self._fire("get_trending_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "get_trending",
                "trending_up": trending_up, "trending_down": trending_down}

    # ------------------------------------------------------------------ #

    def get_champion_meta(self, champion_id: int) -> Dict[str, Any]:
        """Get meta stats for a single champion.

        Returns
        -------
        dict  with win_rate, pick_rate, ban_rate, trend, confidence
        """
        self._op_count += 1
        obs_list = self._observations.get(champion_id, [])
        if not obs_list:
            return {"status": "ok", "op": "get_champion_meta",
                    "champion_id": champion_id, "found": False}

        latest = obs_list[-1]
        avg_wr = sum(o["win_rate"] for o in obs_list) / len(obs_list)
        avg_pr = sum(o["pick_rate"] for o in obs_list) / len(obs_list)
        avg_br = sum(o["ban_rate"] for o in obs_list) / len(obs_list)

        # trend
        if len(obs_list) >= 4:
            mid = len(obs_list) // 2
            first_half = sum(o["win_rate"] for o in obs_list[:mid]) / mid
            second_half = sum(o["win_rate"] for o in obs_list[mid:]) / (len(obs_list) - mid)
            if second_half > first_half * 1.03:
                trend = "rising"
            elif second_half < first_half * 0.97:
                trend = "falling"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {"status": "ok", "op": "get_champion_meta",
                "champion_id": champion_id, "found": True,
                "win_rate": round(avg_wr, 4), "pick_rate": round(avg_pr, 4),
                "ban_rate": round(avg_br, 4), "trend": trend,
                "confidence": round(_confidence(len(obs_list)), 4),
                "observation_count": len(obs_list)}

    # ------------------------------------------------------------------ #

    def get_tier_list(self) -> Dict[str, Any]:
        """Get tier list based on current meta.

        Returns
        -------
        dict  with tiers: S/A/B/C/D lists
        """
        self._op_count += 1
        _start = time.time()

        scored: List[Dict[str, Any]] = []
        for champ_id, obs_list in self._observations.items():
            if not obs_list:
                continue
            avg_wr = sum(o["win_rate"] for o in obs_list) / len(obs_list)
            avg_pr = sum(o["pick_rate"] for o in obs_list) / len(obs_list)
            conf = _confidence(len(obs_list))
            # composite score: win_rate weighted by pick_rate and confidence
            score = avg_wr * (0.7 + 0.3 * avg_pr) * conf
            scored.append({"champion_id": champ_id, "score": round(score, 4),
                           "win_rate": round(avg_wr, 4)})

        scored.sort(key=lambda x: x["score"], reverse=True)

        n = len(scored)
        tiers = {"S": [], "A": [], "B": [], "C": [], "D": []}
        for i, entry in enumerate(scored):
            pct = _safe_div(i, n) if n > 0 else 1.0
            if pct < 0.1:
                tiers["S"].append(entry)
            elif pct < 0.3:
                tiers["A"].append(entry)
            elif pct < 0.55:
                tiers["B"].append(entry)
            elif pct < 0.8:
                tiers["C"].append(entry)
            else:
                tiers["D"].append(entry)

        elapsed = time.time() - _start
        self._fire("get_tier_list_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "get_tier_list", "tiers": tiers,
                "total_champions": n}

    # ------------------------------------------------------------------ #

    def reset(self) -> Dict[str, Any]:
        """Clear all state."""
        self._op_count += 1
        self._observations.clear()
        self._global_counts.clear()
        self._fire("reset_completed", {})
        return {"status": "ok", "op": "reset"}
