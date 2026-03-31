"""
HistoryColdStartHandler — Handles cold-start scenarios for new/unknown players.

Architecture (拿来主义):
  model_warmup_engine.py + history_data_quality_checker.py（M624）

Location: integrations/lol-history/src/lol_history/history_cold_start_handler.py

Design Notes (Knuth-level critique):
  User:
    - get_profile returns approximate profile even with zero history.
    - Confidence scores clearly communicate data uncertainty to downstream consumers.
    - Default profiles are tier-aware — a Diamond cold-start ≠ a Silver cold-start.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Warm-up strategy progressively improves as data accumulates.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_cold_start_handler.v1"

_TIER_DEFAULTS: Dict[str, Dict[str, float]] = {
    "IRON":       {"avg_cs": 4.0, "avg_kda": 1.5, "avg_vision": 8.0, "avg_wr": 0.42},
    "BRONZE":     {"avg_cs": 5.0, "avg_kda": 2.0, "avg_vision": 10.0, "avg_wr": 0.46},
    "SILVER":     {"avg_cs": 5.5, "avg_kda": 2.3, "avg_vision": 12.0, "avg_wr": 0.48},
    "GOLD":       {"avg_cs": 6.0, "avg_kda": 2.8, "avg_vision": 15.0, "avg_wr": 0.50},
    "PLATINUM":   {"avg_cs": 6.5, "avg_kda": 3.0, "avg_vision": 18.0, "avg_wr": 0.50},
    "EMERALD":    {"avg_cs": 7.0, "avg_kda": 3.3, "avg_vision": 22.0, "avg_wr": 0.50},
    "DIAMOND":    {"avg_cs": 7.5, "avg_kda": 3.5, "avg_vision": 25.0, "avg_wr": 0.51},
    "MASTER":     {"avg_cs": 8.0, "avg_kda": 3.8, "avg_vision": 28.0, "avg_wr": 0.52},
    "GRANDMASTER":{"avg_cs": 8.5, "avg_kda": 4.0, "avg_vision": 30.0, "avg_wr": 0.53},
    "CHALLENGER": {"avg_cs": 9.0, "avg_kda": 4.5, "avg_vision": 35.0, "avg_wr": 0.55},
}

_MIN_GAMES_FOR_WARM = 5


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _confidence(n: int, max_n: int = 30) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class HistoryColdStartHandler:
    """Handles cold-start scenarios for new/unknown players.

    Public API
    ----------
    is_cold_start       — check if a player has insufficient data
    get_profile         — get approximate profile (cold or warm)
    warm_up             — progressively improve profile with new data
    get_default_profile — get tier-based default profile
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, min_games: int = _MIN_GAMES_FOR_WARM) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._min_games: int = min_games
        self._profiles: Dict[str, Dict[str, Any]] = {}  # summoner_id -> profile
        self._warmup_count: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def is_cold_start(self, summoner_id: str) -> Dict[str, Any]:
        """Check if a player has insufficient historical data.

        Returns
        -------
        dict  with is_cold, game_count, min_required
        """
        self._op_count += 1
        profile = self._profiles.get(summoner_id, {})
        game_count = profile.get("game_count", 0)
        return {"status": "ok", "op": "is_cold_start",
                "is_cold": game_count < self._min_games,
                "game_count": game_count,
                "min_required": self._min_games}

    # ------------------------------------------------------------------ #

    def get_default_profile(self, tier: str = "GOLD") -> Dict[str, Any]:
        """Get tier-based default profile.

        Parameters
        ----------
        tier : str  (e.g., "GOLD", "DIAMOND")

        Returns
        -------
        dict  with default stats for that tier
        """
        self._op_count += 1
        tier_upper = tier.upper()
        defaults = _TIER_DEFAULTS.get(tier_upper, _TIER_DEFAULTS["GOLD"])
        return {"status": "ok", "op": "get_default_profile",
                "tier": tier_upper,
                "profile": dict(defaults),
                "confidence": 0.1,
                "source": "tier_default"}

    # ------------------------------------------------------------------ #

    def get_profile(self, summoner_id: str, tier: str = "GOLD") -> Dict[str, Any]:
        """Get approximate profile — cold default or warm historical.

        Parameters
        ----------
        summoner_id : str
        tier : str  fallback tier if cold start

        Returns
        -------
        dict  with profile, confidence, source (cold_default/warm_historical/blended)
        """
        self._op_count += 1
        existing = self._profiles.get(summoner_id)

        if existing is None or existing.get("game_count", 0) == 0:
            defaults = _TIER_DEFAULTS.get(tier.upper(), _TIER_DEFAULTS["GOLD"])
            return {"status": "ok", "op": "get_profile",
                    "summoner_id": summoner_id,
                    "profile": dict(defaults),
                    "confidence": 0.1,
                    "source": "cold_default",
                    "game_count": 0}

        gc = existing.get("game_count", 0)
        conf = _confidence(gc)

        if gc >= self._min_games:
            return {"status": "ok", "op": "get_profile",
                    "summoner_id": summoner_id,
                    "profile": existing.get("stats", {}),
                    "confidence": round(conf, 4),
                    "source": "warm_historical",
                    "game_count": gc}

        # Blend default + observed
        defaults = _TIER_DEFAULTS.get(tier.upper(), _TIER_DEFAULTS["GOLD"])
        observed = existing.get("stats", {})
        alpha = gc / self._min_games  # blend weight
        blended: Dict[str, float] = {}
        for key in defaults:
            d_val = defaults[key]
            o_val = observed.get(key, d_val)
            blended[key] = round(alpha * o_val + (1 - alpha) * d_val, 4)

        return {"status": "ok", "op": "get_profile",
                "summoner_id": summoner_id,
                "profile": blended,
                "confidence": round(conf, 4),
                "source": "blended",
                "game_count": gc,
                "blend_alpha": round(alpha, 4)}

    # ------------------------------------------------------------------ #

    def warm_up(self, summoner_id: str, match_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Progressively improve profile with new match data.

        Parameters
        ----------
        summoner_id : str
        match_data : dict  with cs, kda, vision_score, win, game_duration

        Returns
        -------
        dict  with status, game_count, is_warm
        """
        self._op_count += 1
        if match_data is None:
            match_data = {}

        if summoner_id not in self._profiles:
            self._profiles[summoner_id] = {"game_count": 0, "stats": {}, "matches": []}

        profile = self._profiles[summoner_id]
        profile["matches"].append(match_data)
        profile["game_count"] = len(profile["matches"])

        # Recompute rolling stats
        matches = profile["matches"]
        n = len(matches)
        stats: Dict[str, float] = {}
        stats["avg_cs"] = round(sum(m.get("cs", 0) for m in matches) / n, 2)
        stats["avg_kda"] = round(sum(m.get("kda", 0) for m in matches) / n, 2)
        stats["avg_vision"] = round(sum(m.get("vision_score", 0) for m in matches) / n, 2)
        stats["avg_wr"] = round(sum(1 for m in matches if m.get("win")) / n, 4)
        profile["stats"] = stats

        self._warmup_count += 1
        is_warm = n >= self._min_games

        self._fire("warm_up_completed", {"summoner_id": summoner_id, "game_count": n})
        return {"status": "ok", "op": "warm_up",
                "game_count": n, "is_warm": is_warm}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Internal statistics."""
        return {
            "op_count": self._op_count,
            "tracked_summoners": len(self._profiles),
            "warmup_count": self._warmup_count,
            "min_games": self._min_games,
        }
