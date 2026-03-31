"""
GamePhaseDetector — Detects current game phase based on time and events.

Architecture (拿来主义):
  game_phase_strategy_mapper.py（M642）— phase→strategy mapping
  DI-star/distar/agent/default/agent.py — _get_time_factor

Location: integrations/lol-history/src/lol_history/game_phase_detector.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.game_phase_detector.v1"

_DEFAULT_PHASE_THRESHOLDS = {"early_game": (0, 900), "mid_game": (900, 1800), "late_game": (1800, 2700), "endgame": (2700, float("inf"))}

class GamePhaseDetector:
    """Detects game phase: early_game/mid_game/late_game/endgame.

    Public API: detect, register_game_phases, register_hook, get_current_phase, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._phase_defs: Dict[str, Dict] = {"default": dict(_DEFAULT_PHASE_THRESHOLDS)}
        self._hooks: Dict[str, List[Callable]] = {}
        self._current_phase = "early_game"
        self._phase_history: List[Dict] = []
        self._detect_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_game_phases(self, game: str, phases: Dict[str, tuple]) -> Dict[str, Any]:
        self._op_count += 1
        self._phase_defs[game] = phases
        return {"status": "ok", "game": game, "phases": list(phases.keys())}

    def register_hook(self, phase_change: str, hook: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._hooks.setdefault(phase_change, []).append(hook)
        return {"status": "ok", "hook": phase_change}

    def detect(self, game_time: float, game: str = "default", events: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._detect_count += 1
        phases = self._phase_defs.get(game, self._phase_defs["default"])
        detected = "unknown"
        for phase, (lo, hi) in phases.items():
            if lo <= game_time < hi:
                detected = phase
                break
        old = self._current_phase
        if detected != old:
            key = f"{old}→{detected}"
            for h in self._hooks.get(key, []):
                try: h(old, detected, game_time)
                except Exception: pass
            self._phase_history.append({"from": old, "to": detected, "game_time": game_time, "at": time.time()})
            self._current_phase = detected
            self._fire("phase_changed", {"from": old, "to": detected, "game_time": game_time})
        return {"status": "ok", "phase": detected, "game_time": game_time, "changed": detected != old}

    def get_current_phase(self) -> str: return self._current_phase
    def get_stats(self) -> Dict[str, Any]:
        return {"current_phase": self._current_phase, "detections": self._detect_count,
                "phase_changes": len(self._phase_history), "total_ops": self._op_count,
                "registered_games": list(self._phase_defs.keys())}

