"""
LiveCoachingHistoryAdapter — Injects historical context into the real-time coaching engine.

Architecture (拿来主义):
  real_time_coaching_engine.py + live_history_fusion_engine.py（M614）

Location: integrations/lol-history/src/lol_history/live_coaching_history_adapter.py

Design Notes (Knuth-level critique):
  User:
    - adapt() enriches coaching advice with historical patterns without blocking.
    - Missing history gracefully degrades — coaching still works, just less informed.
    - Output includes history_confidence so downstream can weigh accordingly.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - History weight decays over game time (early game = high weight, late = low).
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.live_coaching_history_adapter.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class LiveCoachingHistoryAdapter:
    """Injects historical context into the real-time coaching engine.

    Public API
    ----------
    set_history         — load historical context
    adapt               — enrich a coaching suggestion with history
    adapt_batch         — enrich multiple suggestions
    get_weight_at_time  — compute history weight for a game timestamp
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, history_half_life: float = 1200.0) -> None:
        """
        Parameters
        ----------
        history_half_life : float
            Game-time (seconds) at which history weight drops to 0.5.
            Default 1200s = 20 minutes.
        """
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._history_context: Dict[str, Any] = {}
        self._half_life: float = history_half_life
        self._adapt_count: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def set_history(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Load historical context for adaptation.

        Parameters
        ----------
        context : dict
            Keys: matchup_history, champion_stats, opponent_tendencies, etc.

        Returns
        -------
        dict  with status, context_keys
        """
        self._op_count += 1
        if context is None:
            context = {}
        self._history_context = dict(context)
        self._fire("set_history", {"context_keys": list(context.keys())})
        return {"status": "ok", "op": "set_history",
                "context_keys": list(context.keys())}

    # ------------------------------------------------------------------ #

    def get_weight_at_time(self, game_time: float) -> float:
        """Compute history weight for a given game timestamp.

        Uses exponential decay: weight = exp(-ln(2) * game_time / half_life).

        Parameters
        ----------
        game_time : float
            Seconds since game start.

        Returns
        -------
        float  in [0, 1]
        """
        if game_time <= 0:
            return 1.0
        decay = math.exp(-math.log(2) * game_time / self._half_life)
        return max(0.0, min(1.0, decay))

    # ------------------------------------------------------------------ #

    def adapt(self, suggestion: Dict[str, Any] = None) -> Dict[str, Any]:
        """Enrich a coaching suggestion with historical context.

        Parameters
        ----------
        suggestion : dict
            Must contain game_time, advice_type, advice_text.

        Returns
        -------
        dict  with status, enriched suggestion including history_annotations
        """
        self._op_count += 1
        _start = time.time()
        if suggestion is None:
            suggestion = {}

        game_time = suggestion.get("game_time", 0.0)
        advice_type = suggestion.get("advice_type", "")
        advice_text = suggestion.get("advice_text", "")

        weight = self.get_weight_at_time(game_time)
        annotations: List[str] = []

        # Enrich based on available history
        matchup = self._history_context.get("matchup_history", {})
        if matchup and advice_type in ("trade", "engage", "all_in", "positioning"):
            wr = matchup.get("win_rate")
            if wr is not None:
                if wr < 0.45:
                    annotations.append("Historical matchup is unfavorable — play cautious.")
                elif wr > 0.55:
                    annotations.append("Historical matchup is favorable — press advantage.")

        opp = self._history_context.get("opponent_tendencies", {})
        if opp.get("aggressive") and advice_type in ("positioning", "ward"):
            annotations.append("Opponent historically plays aggressively — respect spacing.")

        champ_stats = self._history_context.get("champion_stats", {})
        if champ_stats.get("power_spike_times"):
            spikes = champ_stats["power_spike_times"]
            for spike_time in spikes:
                if abs(game_time - spike_time) < 60:
                    annotations.append(f"Power spike window at {spike_time}s — look for plays.")

        enriched = {
            **suggestion,
            "history_weight": round(weight, 4),
            "history_annotations": annotations,
            "history_confidence": round(weight * min(1.0, len(annotations) * 0.3 + 0.4), 4),
            "adapted_at": time.time(),
        }

        self._adapt_count += 1
        elapsed = time.time() - _start
        self._fire("adapt_completed", {"elapsed": elapsed, "annotation_count": len(annotations)})
        return {"status": "ok", "op": "adapt", "enriched": enriched}

    # ------------------------------------------------------------------ #

    def adapt_batch(self, suggestions: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Enrich multiple coaching suggestions."""
        self._op_count += 1
        _start = time.time()
        if suggestions is None:
            suggestions = []

        enriched = []
        for s in suggestions:
            result = self.adapt(s)
            enriched.append(result.get("enriched", s))

        elapsed = time.time() - _start
        self._fire("adapt_batch_completed", {"elapsed": elapsed, "count": len(suggestions)})
        return {"status": "ok", "op": "adapt_batch",
                "enriched": enriched, "count": len(suggestions)}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Internal statistics."""
        return {
            "op_count": self._op_count,
            "adapt_count": self._adapt_count,
            "history_loaded": bool(self._history_context),
            "history_keys": list(self._history_context.keys()),
            "half_life": self._half_life,
        }
