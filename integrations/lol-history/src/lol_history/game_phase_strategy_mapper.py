"""
GamePhaseStrategyMapper — Maps game phases to optimal strategies from history.

Architecture (拿来主义):
  decision_engine.py + game_event_pattern_library.py（M615）

Location: integrations/lol-history/src/lol_history/game_phase_strategy_mapper.py

Design Notes (Knuth-level critique):
  User:
    - get_strategy returns phase-specific strategy even with partial data.
    - Phase boundaries are configurable — not hardcoded to specific game times.
    - Strategies include confidence scores so user knows reliability.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Strategy scoring combines historical win-rate with pattern frequency.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.game_phase_strategy_mapper.v1"

_DEFAULT_PHASES: Dict[str, Tuple[float, float]] = {
    "early": (0, 840),        # 0-14 min
    "mid": (840, 1680),       # 14-28 min
    "late": (1680, 99999),    # 28+ min
}

_STRATEGY_TEMPLATES: Dict[str, List[str]] = {
    "early": ["farm_safely", "trade_aggressively", "roam_early", "freeze_lane",
              "push_for_plates", "invade_jungle"],
    "mid": ["group_for_objectives", "split_push", "pick_fights", "secure_vision",
            "rotate_for_dragon", "control_rift_herald"],
    "late": ["team_fight", "baron_play", "elder_dragon_setup", "base_defense",
             "flanking_engage", "peel_for_carry"],
}


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class GamePhaseStrategyMapper:
    """Maps game phases to optimal strategies from historical patterns.

    Public API
    ----------
    register_pattern    — register a historical pattern with outcome
    get_strategy        — get optimal strategy for current game state
    get_phase           — determine current game phase from time
    get_phase_stats     — get statistics for a specific phase
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, phases: Dict[str, Tuple[float, float]] = None) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._phases: Dict[str, Tuple[float, float]] = phases or dict(_DEFAULT_PHASES)
        # phase -> strategy -> {wins, total}
        self._pattern_stats: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"wins": 0, "total": 0})
        )
        self._pattern_count: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def get_phase(self, game_time: float) -> str:
        """Determine current game phase from time.

        Parameters
        ----------
        game_time : float  seconds since game start

        Returns
        -------
        str  phase name
        """
        for phase_name, (start, end) in self._phases.items():
            if start <= game_time < end:
                return phase_name
        return "late"  # default

    # ------------------------------------------------------------------ #

    def register_pattern(self, game_time: float, strategy: str,
                         won: bool, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Register a historical pattern with outcome.

        Parameters
        ----------
        game_time : float
        strategy : str
        won : bool
        metadata : dict

        Returns
        -------
        dict
        """
        self._op_count += 1
        phase = self.get_phase(game_time)
        stats = self._pattern_stats[phase][strategy]
        stats["total"] += 1
        if won:
            stats["wins"] += 1
        self._pattern_count += 1

        self._fire("register_pattern", {"phase": phase, "strategy": strategy})
        return {"status": "ok", "op": "register_pattern",
                "phase": phase, "strategy": strategy,
                "win_rate": round(_safe_div(stats["wins"], stats["total"]), 4)}

    # ------------------------------------------------------------------ #

    def get_strategy(self, game_time: float, game_state: Dict[str, Any] = None,
                     top_n: int = 3) -> Dict[str, Any]:
        """Get optimal strategy for current game state.

        Parameters
        ----------
        game_time : float
        game_state : dict  optional context (gold_diff, objectives, etc.)
        top_n : int

        Returns
        -------
        dict  with status, phase, strategies (list of scored strategies)
        """
        self._op_count += 1
        _start = time.time()
        if game_state is None:
            game_state = {}

        phase = self.get_phase(game_time)
        phase_patterns = self._pattern_stats.get(phase, {})

        scored: List[Dict[str, Any]] = []

        if phase_patterns:
            for strategy, stats in phase_patterns.items():
                wr = _safe_div(stats["wins"], stats["total"], 0.5)
                sample_size = stats["total"]
                # Score combines win rate with sample confidence
                confidence = min(1.0, sample_size / 20.0)
                score = wr * confidence
                scored.append({
                    "strategy": strategy,
                    "score": round(score, 4),
                    "win_rate": round(wr, 4),
                    "sample_size": sample_size,
                    "confidence": round(confidence, 4),
                })
        else:
            # Cold start: return template strategies with default scores
            templates = _STRATEGY_TEMPLATES.get(phase, [])
            for s in templates:
                scored.append({
                    "strategy": s,
                    "score": 0.5,
                    "win_rate": 0.5,
                    "sample_size": 0,
                    "confidence": 0.0,
                })

        scored.sort(key=lambda x: -x["score"])
        strategies = scored[:top_n]

        # Game state modifiers
        gold_diff = game_state.get("gold_diff", 0)
        if gold_diff > 3000:
            for s in strategies:
                if s["strategy"] in ("push_for_plates", "trade_aggressively",
                                      "group_for_objectives", "baron_play"):
                    s["score"] = round(min(1.0, s["score"] + 0.1), 4)
                    s["modifier"] = "ahead_in_gold"

        elapsed = time.time() - _start
        self._fire("get_strategy_completed", {"elapsed": elapsed, "phase": phase})
        return {"status": "ok", "op": "get_strategy",
                "phase": phase, "game_time": game_time,
                "strategies": strategies}

    # ------------------------------------------------------------------ #

    def get_phase_stats(self, phase: str) -> Dict[str, Any]:
        """Get statistics for a specific phase.

        Returns
        -------
        dict  with strategy stats for the phase
        """
        self._op_count += 1
        patterns = self._pattern_stats.get(phase, {})
        stats: List[Dict[str, Any]] = []
        for strategy, s in patterns.items():
            stats.append({
                "strategy": strategy,
                "wins": s["wins"],
                "total": s["total"],
                "win_rate": round(_safe_div(s["wins"], s["total"]), 4),
            })
        stats.sort(key=lambda x: -x["win_rate"])
        return {"status": "ok", "op": "get_phase_stats",
                "phase": phase, "strategies": stats}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "pattern_count": self._pattern_count,
            "phases": list(self._phases.keys()),
            "tracked_phases": list(self._pattern_stats.keys()),
        }
